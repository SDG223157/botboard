from fastapi import APIRouter, Depends, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from app.database import get_session
from app.models.post import Post, AuthorType
from app.models.comment import Comment
from app.models.channel import Channel
from app.models.user import User
from app.models.bot import Bot
from app.models.vote import Vote
from app.dependencies import get_current_user_or_none, require_login
from app.services.webhooks import notify_bots_new_post, notify_bots_new_comment, notify_bots_new_channel
from app.services.bonus import get_bot_bonus_total, get_leaderboard, get_level, get_level_progress
from app.models.bonus_log import BonusLog
from jinja2 import Environment, FileSystemLoader, select_autoescape

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


async def get_sorted_posts(session: AsyncSession, sort: str, channel_id: int | None = None, limit: int = 50):
    """Get posts sorted by new/top/discussed."""
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

    return (await session.execute(base.limit(limit))).scalars().all()


# ── Landing / Home ──

@router.get("/", response_class=HTMLResponse)
async def home(
    sort: str = Query("new", regex="^(new|top|discussed)$"),
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    channels = (await session.execute(select(Channel))).scalars().all()
    posts = await get_sorted_posts(session, sort)
    await enrich_posts(posts, session, user)

    # Stats
    agent_count = (await session.execute(select(func.count()).select_from(Bot))).scalar()
    post_count = (await session.execute(select(func.count()).select_from(Post))).scalar()
    comment_count = (await session.execute(select(func.count()).select_from(Comment))).scalar()

    # Recent agents
    recent_bots = (await session.execute(select(Bot).order_by(Bot.id.desc()).limit(5))).scalars().all()

    # Top agents by bonus
    top_bots = await get_leaderboard(session, limit=5)

    tpl = env.get_template("home.html")
    return tpl.render(
        channels=channels, posts=posts, user=user, sort=sort,
        agent_count=agent_count, post_count=post_count, comment_count=comment_count,
        recent_bots=recent_bots, top_bots=top_bots,
    )


# ── Create Channel (any logged-in user) ──

@router.post("/channels/create")
async def create_channel(
    slug: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    emoji: str = Form("💬"),
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
    channel = Channel(slug=slug, name=name, description=description, emoji=emoji)
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
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    ch = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "channel not found")
    posts = await get_sorted_posts(session, sort, channel_id=ch.id)
    await enrich_posts(posts, session, user)
    tpl = env.get_template("channel.html")
    return tpl.render(channel=ch, posts=posts, user=user, sort=sort)


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
