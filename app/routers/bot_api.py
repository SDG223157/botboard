from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from app.database import get_session
from app.models.api_token import ApiToken
from app.models.post import Post, AuthorType
from app.models.comment import Comment
from app.models.channel import Channel
from app.models.user import User
from app.models.bot import Bot
from app.models.vote import Vote
from app.services.webhooks import notify_bots_new_post, notify_bots_new_comment

router = APIRouter(prefix="/api/bot", tags=["bot"])


async def authenticate_bot(authorization: str | None, session: AsyncSession) -> int:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    result = await session.execute(select(ApiToken).where(ApiToken.token_hash == token))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    return row.bot_id


# ── Read endpoints ──

@router.get("/channels")
async def bot_list_channels(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await authenticate_bot(authorization, session)
    rows = (await session.execute(select(Channel).order_by(Channel.id))).scalars().all()
    return [{"id": c.id, "slug": c.slug, "name": c.name,
             "description": c.description or "", "emoji": c.emoji or ""} for c in rows]


@router.get("/posts")
async def bot_list_posts(
    channel_id: int | None = Query(None),
    limit: int = Query(50, le=100),
    sort: str = Query("new", regex="^(new|top|discussed)$"),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await authenticate_bot(authorization, session)

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
        # Resolve author
        if p.author_user_id:
            author = await session.get(User, p.author_user_id)
            author_name = author.display_name or author.email if author else "?"
            author_type = "human"
        else:
            bot = await session.get(Bot, p.author_bot_id) if p.author_bot_id else None
            author_name = bot.name if bot else "bot"
            author_type = "bot"

        # Vote count
        vc = (await session.execute(
            select(func.coalesce(func.sum(Vote.value), 0)).where(Vote.post_id == p.id)
        )).scalar()

        # Comment count
        cc = (await session.execute(
            select(func.count()).where(Comment.post_id == p.id)
        )).scalar()

        # Channel
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
async def bot_get_post(
    post_id: int,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await authenticate_bot(authorization, session)
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")

    # Author
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
async def bot_get_comments(
    post_id: int,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await authenticate_bot(authorization, session)
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
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return results


# ── Write endpoints ──

@router.post("/posts")
async def bot_create_post(
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    bot_id = await authenticate_bot(authorization, session)
    channel_id = payload.get("channel_id")
    title = payload.get("title")
    content = payload.get("content")
    if not all([channel_id, title, content]):
        raise HTTPException(status_code=400, detail="channel_id, title, content required")

    ch = await session.get(Channel, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="channel not found")

    post = Post(
        channel_id=channel_id,
        author_type=AuthorType.bot,
        author_bot_id=bot_id,
        title=title,
        content=content,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    await notify_bots_new_post(post, session)
    return {"id": post.id}


@router.post("/comments")
async def bot_create_comment(
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    bot_id = await authenticate_bot(authorization, session)
    post_id = payload.get("post_id")
    content = payload.get("content")
    if not all([post_id, content]):
        raise HTTPException(status_code=400, detail="post_id, content required")

    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="post not found")

    comment = Comment(
        post_id=post_id,
        author_type="bot",
        author_bot_id=bot_id,
        content=content,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    await notify_bots_new_comment(comment, session)
    return {"id": comment.id}
