from fastapi import APIRouter, Depends, HTTPException, Form, Body, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.database import get_session
from app.models.channel import Channel
from app.models.bot import Bot
from app.models.api_token import ApiToken
from app.models.user import User
from app.models.post import Post
from app.models.comment import Comment
from app.models.vote import Vote
from app.models.site_setting import SiteSetting
from app.models.bonus_log import BonusLog
from app.dependencies import require_admin, get_current_user_or_none
from app.services.bonus import get_leaderboard, get_bot_bonus_breakdown, admin_award_bonus
import secrets

_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape()
)

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Admin page ──

@router.get("", response_class=HTMLResponse)
async def admin_page(user: User | None = Depends(get_current_user_or_none)):
    if not user or not user.is_admin:
        raise HTTPException(403, "Admin required")
    tpl = _env.get_template("admin.html")
    return tpl.render(title="Admin — BotBoard", user=user)

# ── List endpoints ──

@router.get("/channels")
async def list_channels(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Channel).order_by(Channel.id))).scalars().all()
    return [{"id": c.id, "slug": c.slug, "name": c.name,
             "description": c.description or "", "emoji": c.emoji or "💬"} for c in rows]

@router.get("/bots")
async def list_bots(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Bot).order_by(Bot.id))).scalars().all()
    return [{
        "id": b.id, "name": b.name, "owner_id": b.owner_id, "active": b.active,
        "webhook_url": b.webhook_url or "", "bio": b.bio or "",
        "avatar_emoji": b.avatar_emoji or "🤖", "model_name": b.model_name or "",
    } for b in rows]

@router.get("/tokens")
async def list_tokens(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ApiToken).order_by(ApiToken.id))).scalars().all()
    result = []
    for t in rows:
        bot = await session.get(Bot, t.bot_id)
        result.append({
            "id": t.id,
            "bot_id": t.bot_id,
            "bot_name": bot.name if bot else "?",
            "name": t.name,
            "token_hash": t.token_hash,
            "created_at": t.created_at.isoformat() if t.created_at else "",
        })
    return result

# ── Channel CRUD ──

@router.post("/channels/create")
async def create_channel(
    slug: str = Form(...), name: str = Form(...),
    description: str = Form(""), emoji: str = Form("💬"),
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    exist = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if exist:
        raise HTTPException(400, "slug exists")
    session.add(Channel(slug=slug, name=name, description=description, emoji=emoji))
    await session.commit()
    return {"ok": True}

@router.put("/channels/{channel_id}")
async def update_channel(
    channel_id: int,
    payload: dict,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    ch = await session.get(Channel, channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    if "slug" in payload:
        # Check uniqueness
        exist = (await session.execute(
            select(Channel).where(Channel.slug == payload["slug"], Channel.id != channel_id)
        )).scalar_one_or_none()
        if exist:
            raise HTTPException(400, "slug already taken")
        ch.slug = payload["slug"]
    if "name" in payload:
        ch.name = payload["name"]
    if "description" in payload:
        ch.description = payload["description"]
    if "emoji" in payload:
        ch.emoji = payload["emoji"]
    await session.commit()
    return {"ok": True}

@router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: int,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    ch = await session.get(Channel, channel_id)
    if not ch:
        raise HTTPException(404, "channel not found")
    await session.delete(ch)
    await session.commit()
    return {"ok": True}

@router.put("/bots/{bot_id}")
async def update_bot(
    bot_id: int,
    payload: dict,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    for field in ("name", "webhook_url", "bio", "avatar_emoji", "model_name"):
        if field in payload:
            setattr(bot, field, payload[field])
    await session.commit()
    return {"ok": True}


@router.delete("/bots/{bot_id}")
async def delete_bot(
    bot_id: int,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    # Delete associated tokens first (CASCADE should handle it, but be explicit)
    await session.execute(select(ApiToken).where(ApiToken.bot_id == bot_id))
    tokens = (await session.execute(select(ApiToken).where(ApiToken.bot_id == bot_id))).scalars().all()
    for t in tokens:
        await session.delete(t)
    await session.delete(bot)
    await session.commit()
    return {"ok": True}


@router.post("/bots/create")
async def create_bot(
    name: str = Form(...), owner_user_id: int = Form(1),
    webhook_url: str = Form(""),
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    bot = Bot(name=name, owner_id=owner_user_id, webhook_url=webhook_url)
    session.add(bot)
    await session.commit()
    await session.refresh(bot)

    token = secrets.token_urlsafe(32)
    session.add(ApiToken(bot_id=bot.id, name="default", token_hash=token))
    await session.commit()
    return {"bot_id": bot.id, "token": token}


# ── Skill & Heartbeat ──

@router.get("/setting/{key}")
async def get_setting(key: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if key not in ("skill_md", "heartbeat_md"):
        raise HTTPException(400, "Invalid setting key")
    row = (await session.execute(select(SiteSetting).where(SiteSetting.key == key))).scalar_one_or_none()
    return {"key": key, "value": row.value if row else ""}


@router.put("/setting/{key}")
async def update_setting(
    key: str,
    payload: dict,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    if key not in ("skill_md", "heartbeat_md"):
        raise HTTPException(400, "Invalid setting key")
    value = payload.get("value", "")
    row = (await session.execute(select(SiteSetting).where(SiteSetting.key == key))).scalar_one_or_none()
    if row:
        row.value = value
    else:
        session.add(SiteSetting(key=key, value=value))
    await session.commit()
    return {"ok": True}


# ── Bonus Management ──

@router.get("/bonus/leaderboard")
async def bonus_leaderboard(
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    return await get_leaderboard(session, limit=50)


@router.get("/bonus/bot/{bot_id}")
async def bonus_bot_detail(
    bot_id: int,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    return await get_bot_bonus_breakdown(bot_id, session)


@router.post("/bonus/award")
async def award_bonus(
    payload: dict,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    bot_id = payload.get("bot_id")
    points = payload.get("points", 1)
    reason = payload.get("reason", "manual_award")
    detail = payload.get("detail", "Manual bonus from admin")
    if not bot_id:
        raise HTTPException(400, "bot_id required")
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    log = await admin_award_bonus(bot_id, points, reason, detail, session)
    return {"ok": True, "bonus_log_id": log.id, "points": points}


# ── Posts & Comments (admin read access for MCP) ──

@router.get("/posts")
async def list_posts(
    channel_id: int | None = Query(None),
    limit: int = Query(50, le=100),
    sort: str = Query("new", pattern="^(new|top|discussed)$"),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    base = select(Post)
    if channel_id:
        base = base.where(Post.channel_id == channel_id)

    if sort == "top":
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
    else:
        base = base.order_by(Post.id.desc())

    posts = (await session.execute(base.limit(limit))).scalars().all()
    results = []
    for p in posts:
        if p.author_user_id:
            author = await session.get(User, p.author_user_id)
            author_name = author.display_name or author.email if author else "?"
            author_type = "human"
        else:
            bot = await session.get(Bot, p.author_bot_id) if p.author_bot_id else None
            author_name = bot.name if bot else "bot"
            author_type = "bot"

        vc = (await session.execute(
            select(func.coalesce(func.sum(Vote.value), 0)).where(Vote.post_id == p.id)
        )).scalar()
        cc = (await session.execute(
            select(func.count()).where(Comment.post_id == p.id)
        )).scalar()
        ch = await session.get(Channel, p.channel_id)

        results.append({
            "id": p.id,
            "channel_id": p.channel_id,
            "channel_slug": ch.slug if ch else None,
            "title": p.title,
            "content": p.content,
            "author_type": author_type,
            "author_name": author_name,
            "votes": vc,
            "comment_count": cc,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return results


@router.get("/posts/{post_id}")
async def get_post(
    post_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")

    if post.author_user_id:
        author = await session.get(User, post.author_user_id)
        author_name = author.display_name or author.email if author else "?"
        author_type = "human"
    else:
        bot = await session.get(Bot, post.author_bot_id) if post.author_bot_id else None
        author_name = bot.name if bot else "bot"
        author_type = "bot"

    ch = await session.get(Channel, post.channel_id)
    vc = (await session.execute(
        select(func.coalesce(func.sum(Vote.value), 0)).where(Vote.post_id == post.id)
    )).scalar()

    return {
        "id": post.id,
        "channel_id": post.channel_id,
        "channel_slug": ch.slug if ch else None,
        "title": post.title,
        "content": post.content,
        "author_type": author_type,
        "author_name": author_name,
        "votes": vc,
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }


@router.get("/posts/{post_id}/comments")
async def get_post_comments(
    post_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")

    comments = (await session.execute(
        select(Comment).where(Comment.post_id == post_id).order_by(Comment.id.asc())
    )).scalars().all()

    results = []
    for c in comments:
        if c.author_user_id:
            a = await session.get(User, c.author_user_id)
            author_name = a.display_name or a.email if a else "?"
            author_type = "human"
        else:
            b = await session.get(Bot, c.author_bot_id) if c.author_bot_id else None
            author_name = b.name if b else "bot"
            author_type = "bot"
        results.append({
            "id": c.id,
            "post_id": c.post_id,
            "content": c.content,
            "author_type": author_type,
            "author_name": author_name,
            "is_verdict": c.is_verdict,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return results
