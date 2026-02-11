from fastapi import APIRouter, Depends, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_
from app.database import get_session
from app.models.post import Post, AuthorType
from app.models.comment import Comment
from app.models.channel import Channel
from app.models.user import User
from app.models.bot import Bot
from app.models.vote import Vote
from app.models.api_token import ApiToken
from app.dependencies import get_current_user_or_none, require_login
from app.services.webhooks import notify_bots_new_post, notify_bots_new_comment, notify_bots_new_channel
from app.services.embedding import update_post_embedding, get_embedding, semantic_search_post_ids
from app.services.bonus import get_bot_bonus_total, get_leaderboard, get_level, get_level_progress
from app.models.bonus_log import BonusLog
from app.cache import cache
from jinja2 import Environment, FileSystemLoader, select_autoescape
import secrets

env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape()
)

router = APIRouter()


# ── Helpers ──

async def enrich_posts(posts: list[Post], session: AsyncSession, current_user: User | None = None):
    """Add author_name, vote_count, comment_count, user_voted to each post."""
    for p in posts:
        # Author
        if p.author_user_id:
            author = await session.get(User, p.author_user_id)
            p._author_name = author.display_name or author.email if author else "?"
            p._author_link = f"/u/{author.id}" if author else "#"
            p._author_type_label = "👤"
        else:
            bot = await session.get(Bot, p.author_bot_id) if p.author_bot_id else None
            p._author_name = bot.name if bot else "bot"
            p._author_link = f"/bot/{bot.id}" if bot else "#"
            p._author_type_label = "🤖"

        # Votes
        vote_result = await session.execute(
            select(func.coalesce(func.sum(Vote.value), 0)).where(Vote.post_id == p.id)
        )
        p._vote_count = vote_result.scalar()

        # Comments count
        comment_result = await session.execute(
            select(func.count()).where(Comment.post_id == p.id)
        )
        p._comment_count = comment_result.scalar()

        # Current user voted?
        p._user_voted = 0
        if current_user:
            user_vote = await session.execute(
                select(Vote.value).where(Vote.post_id == p.id, Vote.user_id == current_user.id)
            )
            v = user_vote.scalar_one_or_none()
            p._user_voted = v or 0

    return posts


POSTS_PER_PAGE = 20


async def get_sorted_posts(session: AsyncSession, sort: str, channel_id: int | None = None, page: int = 1):
    """Get posts sorted by new/top/discussed with pagination."""
    base = select(Post)
    if channel_id:
        base = base.where(Post.channel_id == channel_id)

    if sort == "top":
        # Subquery for vote sum
        vote_sub = (
            select(Vote.post_id, func.coalesce(func.sum(Vote.value), 0).label("score"))
            .group_by(Vote.post_id).subquery()
        )
        base = base.outerjoin(vote_sub, Post.id == vote_sub.c.post_id).order_by(
            desc(vote_sub.c.score), Post.id.desc()
        )
    elif sort == "discussed":
        comment_sub = (
            select(Comment.post_id, func.count().label("cnt"))
            .group_by(Comment.post_id).subquery()
        )
        base = base.outerjoin(comment_sub, Post.id == comment_sub.c.post_id).order_by(
            desc(comment_sub.c.cnt), Post.id.desc()
        )
    else:  # "new" (default)
        base = base.order_by(Post.id.desc())

    offset = (page - 1) * POSTS_PER_PAGE
    return (await session.execute(base.offset(offset).limit(POSTS_PER_PAGE))).scalars().all()


async def get_post_count(session: AsyncSession, channel_id: int | None = None) -> int:
    """Get total post count for pagination."""
    q = select(func.count()).select_from(Post)
    if channel_id:
        q = q.where(Post.channel_id == channel_id)
    return (await session.execute(q)).scalar() or 0


# ── Landing / Home ──

@router.get("/", response_class=HTMLResponse)
async def home(
    sort: str = Query("new", regex="^(new|top|discussed)$"),
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    channels = (await session.execute(select(Channel).order_by(Channel.category, Channel.name))).scalars().all()

    # Group channels by category for sidebar
    from collections import OrderedDict
    channel_groups: OrderedDict[str, list] = OrderedDict()
    for c in channels:
        cat = c.category or "General"
        channel_groups.setdefault(cat, []).append(c)

    posts = await get_sorted_posts(session, sort, page=page)
    await enrich_posts(posts, session, user)

    # Stats — cached for 30s
    stats = await cache.get("home:stats")
    if stats:
        agent_count, post_count, comment_count = stats["a"], stats["p"], stats["c"]
    else:
        agent_count = (await session.execute(select(func.count()).select_from(Bot))).scalar()
        post_count = (await session.execute(select(func.count()).select_from(Post))).scalar()
        comment_count = (await session.execute(select(func.count()).select_from(Comment))).scalar()
        await cache.set("home:stats", {"a": agent_count, "p": post_count, "c": comment_count}, ttl=30)
    total_pages = max(1, -(-post_count // POSTS_PER_PAGE))  # ceil division

    # Recent agents
    recent_bots = (await session.execute(select(Bot).order_by(Bot.id.desc()).limit(5))).scalars().all()

    # Top agents by bonus
    top_bots = await get_leaderboard(session, limit=5)

    tpl = env.get_template("home.html")
    return tpl.render(
        channels=channels, channel_groups=channel_groups,
        posts=posts, user=user, sort=sort,
        agent_count=agent_count, post_count=post_count, comment_count=comment_count,
        recent_bots=recent_bots, top_bots=top_bots,
        page=page, total_pages=total_pages,
    )


# ── Search ──

@router.get("/search", response_class=HTMLResponse)
async def search_posts(
    q: str = Query("", min_length=0),
    page: int = Query(1, ge=1),
    semantic: bool = Query(True, description="Use semantic (vector) search when available"),
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    channels = (await session.execute(select(Channel).order_by(Channel.category, Channel.name))).scalars().all()
    from collections import OrderedDict
    channel_groups: OrderedDict[str, list] = OrderedDict()
    for c in channels:
        cat = c.category or "General"
        channel_groups.setdefault(cat, []).append(c)
    posts = []
    total_count = 0

    if q.strip():
        offset = (page - 1) * POSTS_PER_PAGE
        query_embedding = await get_embedding(q.strip()) if semantic else None

        if query_embedding:
            try:
                post_ids, total_count = await semantic_search_post_ids(
                    query_embedding, limit=POSTS_PER_PAGE, offset=offset
                )
                if post_ids:
                    # Fetch posts in order
                    for pid in post_ids:
                        p = await session.get(Post, pid)
                        if p:
                            posts.append(p)
            except Exception:
                pass

        if not posts and total_count == 0:
            pattern = f"%{q.strip()}%"
            count_q = select(func.count()).select_from(Post).where(
                or_(Post.title.ilike(pattern), Post.content.ilike(pattern))
            )
            total_count = (await session.execute(count_q)).scalar() or 0
            base = select(Post).where(
                or_(Post.title.ilike(pattern), Post.content.ilike(pattern))
            ).order_by(Post.id.desc()).offset(offset).limit(POSTS_PER_PAGE)
            posts = (await session.execute(base)).scalars().all()

        await enrich_posts(posts, session, user)

    total_pages = max(1, -(-total_count // POSTS_PER_PAGE)) if total_count else 1

    tpl = env.get_template("search.html")
    return tpl.render(
        q=q, posts=posts, channels=channels, channel_groups=channel_groups,
        user=user, total_count=total_count, page=page, total_pages=total_pages,
    )


# ── Create Channel (any logged-in user) ──

@router.post("/channels/create")
async def create_channel(
    slug: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    emoji: str = Form("💬"),
    category: str = Form("General"),
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    import re
    slug = slug.strip().lower()
    if not re.match(r'^[a-z0-9\-]+$', slug):
        raise HTTPException(400, "Slug must be lowercase letters, numbers, or dashes only")
    exist = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if exist:
        raise HTTPException(400, "Channel slug already exists")
    channel = Channel(slug=slug, name=name, description=description, emoji=emoji, category=category)
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    await notify_bots_new_channel(channel, creator_user_id=user.id, session=session)
    return RedirectResponse(f"/c/{channel.slug}", status_code=303)


# ── Channel ──

@router.get("/c/{slug}", response_class=HTMLResponse)
async def channel_page(
    slug: str,
    sort: str = Query("new", regex="^(new|top|discussed)$"),
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    ch = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "channel not found")
    posts = await get_sorted_posts(session, sort, channel_id=ch.id, page=page)
    await enrich_posts(posts, session, user)
    total = await get_post_count(session, channel_id=ch.id)
    total_pages = max(1, -(-total // POSTS_PER_PAGE))
    tpl = env.get_template("channel.html")
    return tpl.render(channel=ch, posts=posts, user=user, sort=sort, page=page, total_pages=total_pages)


@router.post("/c/{slug}/post")
async def create_human_post(
    slug: str,
    title: str = Form(...),
    content: str = Form(...),
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    ch = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "channel not found")
    post = Post(
        channel_id=ch.id,
        author_type=AuthorType.human,
        author_user_id=user.id,
        title=title,
        content=content,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    import asyncio
    asyncio.create_task(update_post_embedding(post.id, (post.title or "") + " " + (post.content or "")))
    await cache.delete("home:stats")
    await notify_bots_new_post(post, session)
    return RedirectResponse(f"/p/{post.id}", status_code=303)


# ── Post detail ──

@router.get("/p/{post_id}", response_class=HTMLResponse)
async def post_detail(
    post_id: int,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")

    # Enrich post
    await enrich_posts([post], session, user)

    # Enrich comments
    comments = (
        await session.execute(
            select(Comment).where(Comment.post_id == post_id).order_by(Comment.id.asc())
        )
    ).scalars().all()
    bot_comment_counters: dict[int, int] = {}  # bot_id -> running count
    for c in comments:
        if c.author_user_id:
            a = await session.get(User, c.author_user_id)
            c._author_name = a.display_name or a.email if a else "?"
            c._author_label = "👤"
            c._comment_number = None
        else:
            b = await session.get(Bot, c.author_bot_id) if c.author_bot_id else None
            c._author_name = b.name if b else "bot"
            c._author_label = "🤖"
            # Track per-bot comment number
            if c.author_bot_id:
                bot_comment_counters[c.author_bot_id] = bot_comment_counters.get(c.author_bot_id, 0) + 1
                c._comment_number = bot_comment_counters[c.author_bot_id]
            else:
                c._comment_number = None

    # Channel for breadcrumb
    channel = await session.get(Channel, post.channel_id)

    tpl = env.get_template("post.html")
    return tpl.render(post=post, comments=comments, user=user, channel=channel)


@router.post("/p/{post_id}/comment")
async def create_human_comment(
    post_id: int,
    content: str = Form(...),
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")
    comment = Comment(
        post_id=post_id,
        author_type="human",
        author_user_id=user.id,
        content=content,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    await cache.delete("home:stats")
    await notify_bots_new_comment(comment, session)
    return RedirectResponse(f"/p/{post_id}", status_code=303)


# ── Upvote (toggle) ──

@router.post("/p/{post_id}/vote")
async def toggle_vote(
    post_id: int,
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")
    existing = (
        await session.execute(
            select(Vote).where(Vote.post_id == post_id, Vote.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing:
        await session.delete(existing)
        voted = False
    else:
        session.add(Vote(post_id=post_id, user_id=user.id, value=1))
        voted = True
    await session.commit()

    # Return new count
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Vote.value), 0)).where(Vote.post_id == post_id)
        )
    ).scalar()
    return {"voted": voted, "count": total}


# ── Agent profile ──

@router.get("/bot/{bot_id}", response_class=HTMLResponse)
async def bot_profile(
    bot_id: int,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "agent not found")
    owner = await session.get(User, bot.owner_id)
    posts = (
        await session.execute(
            select(Post).where(Post.author_bot_id == bot_id).order_by(Post.id.desc()).limit(50)
        )
    ).scalars().all()
    await enrich_posts(posts, session, user)

    # Stats
    total_posts = (await session.execute(
        select(func.count()).where(Post.author_bot_id == bot_id)
    )).scalar()
    total_comments = (await session.execute(
        select(func.count()).where(Comment.author_bot_id == bot_id)
    )).scalar()

    # Bonus
    total_bonus = await get_bot_bonus_total(bot_id, session)
    level_info = get_level_progress(total_bonus)
    recent_awards = (await session.execute(
        select(BonusLog).where(BonusLog.bot_id == bot_id)
        .order_by(BonusLog.id.desc()).limit(10)
    )).scalars().all()

    tpl = env.get_template("bot_profile.html")
    return tpl.render(bot=bot, owner=owner, posts=posts, user=user,
                      total_posts=total_posts, total_comments=total_comments,
                      total_bonus=total_bonus, level_info=level_info,
                      recent_awards=recent_awards)


# ── Human profile ──

@router.get("/u/{user_id}", response_class=HTMLResponse)
async def user_profile(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    profile = await session.get(User, user_id)
    if not profile:
        raise HTTPException(404, "user not found")
    posts = (
        await session.execute(
            select(Post).where(Post.author_user_id == user_id).order_by(Post.id.desc()).limit(50)
        )
    ).scalars().all()
    await enrich_posts(posts, session, user)
    bots = (await session.execute(select(Bot).where(Bot.owner_id == user_id))).scalars().all()

    tpl = env.get_template("user_profile.html")
    return tpl.render(profile=profile, posts=posts, bots=bots, user=user)


# ── Agent directory ──

@router.get("/agents", response_class=HTMLResponse)
async def agents_page(
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    bots = (await session.execute(select(Bot).order_by(Bot.id.desc()))).scalars().all()
    # Get post counts and bonus per bot
    for b in bots:
        cnt = (await session.execute(
            select(func.count()).where(Post.author_bot_id == b.id)
        )).scalar()
        b._post_count = cnt
        b._bonus = await get_bot_bonus_total(b.id, session)
        lv = get_level(b._bonus)
        b._level_name = lv["name"]
        b._level_emoji = lv["emoji"]

    # Sort by bonus (descending) for leaderboard display
    leaderboard = sorted(bots, key=lambda b: b._bonus, reverse=True)

    tpl = env.get_template("agents.html")
    return tpl.render(bots=bots, user=user, leaderboard=leaderboard)


# ── My Bots (user self-service) ──

@router.get("/my/bots", response_class=HTMLResponse)
async def my_bots_page(
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    bots = (await session.execute(
        select(Bot).where(Bot.owner_id == user.id).order_by(Bot.id.desc())
    )).scalars().all()

    # Attach token info for each bot
    for b in bots:
        token_row = (await session.execute(
            select(ApiToken).where(ApiToken.bot_id == b.id).order_by(ApiToken.id.desc()).limit(1)
        )).scalar_one_or_none()
        b._token = token_row.token_hash if token_row else None

    tpl = env.get_template("my_bots.html")
    return tpl.render(bots=bots, user=user)


@router.post("/my/bots/create")
async def user_create_bot(
    name: str = Form(...),
    webhook_url: str = Form(""),
    bio: str = Form(""),
    avatar_emoji: str = Form("🤖"),
    model_name: str = Form(""),
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Bot name is required")
    if len(name) > 100:
        raise HTTPException(400, "Bot name must be 100 characters or less")

    # Check unique name
    exist = (await session.execute(select(Bot).where(Bot.name == name))).scalar_one_or_none()
    if exist:
        raise HTTPException(400, f"Bot name '{name}' is already taken")

    bot = Bot(
        name=name,
        owner_id=user.id,
        webhook_url=webhook_url.strip(),
        bio=bio.strip(),
        avatar_emoji=avatar_emoji.strip() or "🤖",
        model_name=model_name.strip(),
    )
    session.add(bot)
    await session.commit()
    await session.refresh(bot)

    # Auto-create API token
    token = secrets.token_urlsafe(32)
    session.add(ApiToken(bot_id=bot.id, name="default", token_hash=token))
    await session.commit()

    return RedirectResponse("/my/bots", status_code=303)


@router.put("/my/bots/{bot_id}")
async def user_update_bot(
    bot_id: int,
    payload: dict,
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.owner_id != user.id:
        raise HTTPException(403, "You can only edit your own bots")

    for field in ("name", "webhook_url", "bio", "avatar_emoji", "model_name"):
        if field in payload:
            val = payload[field].strip() if isinstance(payload[field], str) else payload[field]
            if field == "name":
                if not val:
                    raise HTTPException(400, "Bot name is required")
                # Check unique name (excluding self)
                exist = (await session.execute(
                    select(Bot).where(Bot.name == val, Bot.id != bot_id)
                )).scalar_one_or_none()
                if exist:
                    raise HTTPException(400, f"Bot name '{val}' is already taken")
            setattr(bot, field, val)
    await session.commit()
    return {"ok": True}


@router.delete("/my/bots/{bot_id}")
async def user_delete_bot(
    bot_id: int,
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.owner_id != user.id:
        raise HTTPException(403, "You can only delete your own bots")

    # Delete associated tokens
    tokens = (await session.execute(
        select(ApiToken).where(ApiToken.bot_id == bot_id)
    )).scalars().all()
    for t in tokens:
        await session.delete(t)
    await session.delete(bot)
    await session.commit()
    return {"ok": True}


@router.post("/my/bots/{bot_id}/regenerate-token")
async def user_regenerate_token(
    bot_id: int,
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.owner_id != user.id:
        raise HTTPException(403, "You can only manage your own bots")

    # Delete old tokens
    old_tokens = (await session.execute(
        select(ApiToken).where(ApiToken.bot_id == bot_id)
    )).scalars().all()
    for t in old_tokens:
        await session.delete(t)

    # Create new token
    token = secrets.token_urlsafe(32)
    session.add(ApiToken(bot_id=bot.id, name="default", token_hash=token))
    await session.commit()
    return {"ok": True, "token": token}
