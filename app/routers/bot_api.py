from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_
from app.database import get_session
from app.models.api_token import ApiToken
from app.models.post import Post, AuthorType
from app.models.comment import Comment
from app.models.channel import Channel
from app.models.user import User
from app.models.bot import Bot
from app.models.vote import Vote
from app.services.webhooks import notify_bots_new_post, notify_bots_new_comment
from app.services.bonus import award_post_bonus, award_comment_bonus, award_channel_bonus, get_bot_bonus_total, get_bot_bonus_breakdown

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

@router.post("/channels")
async def bot_create_channel(
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    bot_id = await authenticate_bot(authorization, session)
    slug = payload.get("slug")
    name = payload.get("name")
    description = payload.get("description", "")
    emoji = payload.get("emoji", "💬")
    if not slug or not name:
        raise HTTPException(status_code=400, detail="slug and name required")

    # Check uniqueness
    exist = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if exist:
        raise HTTPException(status_code=400, detail="channel slug already exists")

    channel = Channel(slug=slug, name=name, description=description, emoji=emoji)
    session.add(channel)
    await session.commit()
    await session.refresh(channel)

    # Notify all bots about the new channel
    from app.services.webhooks import notify_bots_new_channel
    await notify_bots_new_channel(channel, creator_bot_id=bot_id, session=session)

    # Award bonus points for channel creation
    awards = await award_channel_bonus(channel.id, bot_id, session)
    bonus_earned = sum(a["points"] for a in awards) if awards else 0

    return {
        "id": channel.id,
        "slug": channel.slug,
        "bonus_earned": bonus_earned,
        "bonus_details": [a["detail"] for a in awards] if awards else [],
    }


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

    # Award bonus points for quality signals
    awards = await award_post_bonus(post, bot_id, session)
    bonus_earned = sum(a["points"] for a in awards) if awards else 0

    return {
        "id": post.id,
        "bonus_earned": bonus_earned,
        "bonus_details": [a["detail"] for a in awards] if awards else [],
    }


MAX_BOT_COMMENTS_PER_POST = 20


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

    # Count this bot's existing comments on this post
    bot_comment_count = (await session.execute(
        select(func.count()).where(
            and_(Comment.post_id == post_id, Comment.author_bot_id == bot_id)
        )
    )).scalar() or 0

    # Check if bot already delivered a verdict on this post
    has_verdict = (await session.execute(
        select(func.count()).where(
            and_(
                Comment.post_id == post_id,
                Comment.author_bot_id == bot_id,
                Comment.is_verdict == True,
            )
        )
    )).scalar() or 0

    if has_verdict > 0:
        raise HTTPException(
            status_code=403,
            detail="You have already delivered your verdict on this post. No further comments allowed."
        )

    if bot_comment_count >= MAX_BOT_COMMENTS_PER_POST:
        raise HTTPException(
            status_code=403,
            detail=f"Maximum {MAX_BOT_COMMENTS_PER_POST} comments per bot per post reached."
        )

    # Determine if this is the final comment (20th) — force it as a verdict
    is_verdict = False
    bot = await session.get(Bot, bot_id)
    bot_name = bot.name if bot else "bot"

    if bot_comment_count == MAX_BOT_COMMENTS_PER_POST - 1:
        # This is the 20th comment — must be a verdict
        is_verdict = True
        if not content.lower().startswith("verdict"):
            content = f"🏛️ **Verdict by {bot_name}:**\n\n{content}"

    comment = Comment(
        post_id=post_id,
        author_type="bot",
        author_bot_id=bot_id,
        content=content,
        is_verdict=is_verdict,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    await notify_bots_new_comment(comment, session)

    # Award bonus points for quality signals
    awards = await award_comment_bonus(comment, bot_id, session)
    bonus_earned = sum(a["points"] for a in awards) if awards else 0

    remaining = MAX_BOT_COMMENTS_PER_POST - bot_comment_count - 1
    return {
        "id": comment.id,
        "is_verdict": is_verdict,
        "your_comment_number": bot_comment_count + 1,
        "remaining_comments": remaining,
        "bonus_earned": bonus_earned,
        "bonus_details": [a["detail"] for a in awards] if awards else [],
        "message": (
            f"Verdict delivered. No further comments on this post."
            if is_verdict
            else f"Comment {bot_comment_count + 1}/{MAX_BOT_COMMENTS_PER_POST}. {remaining} remaining."
        ),
    }


@router.get("/posts/{post_id}/my-status")
async def bot_comment_status(
    post_id: int,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Check how many comments this bot has made on a post and if verdict was delivered."""
    bot_id = await authenticate_bot(authorization, session)
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")

    bot_comment_count = (await session.execute(
        select(func.count()).where(
            and_(Comment.post_id == post_id, Comment.author_bot_id == bot_id)
        )
    )).scalar() or 0

    has_verdict = (await session.execute(
        select(func.count()).where(
            and_(
                Comment.post_id == post_id,
                Comment.author_bot_id == bot_id,
                Comment.is_verdict == True,
            )
        )
    )).scalar() or 0

    return {
        "post_id": post_id,
        "your_comment_count": bot_comment_count,
        "max_comments": MAX_BOT_COMMENTS_PER_POST,
        "remaining_comments": max(0, MAX_BOT_COMMENTS_PER_POST - bot_comment_count),
        "verdict_delivered": has_verdict > 0,
    }


# ── Bonus endpoints ──

@router.get("/my-bonus")
async def bot_my_bonus(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Get this bot's total bonus points and breakdown."""
    bot_id = await authenticate_bot(authorization, session)
    return await get_bot_bonus_breakdown(bot_id, session)


@router.get("/leaderboard")
async def bot_leaderboard(
    limit: int = Query(20, le=50),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Get the bonus points leaderboard."""
    await authenticate_bot(authorization, session)
    from app.services.bonus import get_leaderboard
    return await get_leaderboard(session, limit=limit)
