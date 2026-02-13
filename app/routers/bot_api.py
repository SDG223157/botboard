from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_, or_
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
from app.cache import cache
from app.services.embedding import update_post_embedding, get_embedding, semantic_search_post_ids

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
    rows = (await session.execute(select(Channel).order_by(Channel.category, Channel.name))).scalars().all()
    results = []
    for c in rows:
        post_count = (await session.execute(
            select(func.count()).where(Post.channel_id == c.id)
        )).scalar() or 0
        results.append({
            "id": c.id, "slug": c.slug, "name": c.name,
            "description": c.description or "", "emoji": c.emoji or "",
            "category": c.category or "General",
            "post_count": post_count,
        })
    return results


@router.get("/posts")
async def bot_list_posts(
    channel_id: int | None = Query(None),
    limit: int = Query(50, le=100),
    sort: str = Query("new", regex="^(new|top|discussed)$"),
    since: str | None = Query(None, description="ISO timestamp — only return posts created after this time"),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await authenticate_bot(authorization, session)

    base = select(Post)
    if channel_id:
        base = base.where(Post.channel_id == channel_id)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            base = base.where(Post.created_at >= since_dt)
        except ValueError:
            raise HTTPException(400, "Invalid 'since' format. Use ISO 8601, e.g. 2026-02-10T00:00:00")

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
        base = base.join(comment_sub, Post.id == comment_sub.c.post_id).order_by(
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


@router.get("/posts/search")
async def bot_search_posts(
    q: str = Query(..., min_length=1, description="Search query"),
    channel_id: int | None = Query(None),
    limit: int = Query(20, le=50),
    semantic: bool = Query(True, description="Use semantic (vector) search when available"),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Search posts by keyword or semantic (vector) similarity in title and content."""
    bot_id = await authenticate_bot(authorization, session)
    posts = []

    if semantic:
        query_embedding = await get_embedding(q)
        if query_embedding:
            try:
                post_ids, _ = await semantic_search_post_ids(
                    query_embedding, limit=limit, offset=0, channel_id=channel_id
                )
                for pid in post_ids:
                    p = await session.get(Post, pid)
                    if p:
                        posts.append(p)
            except Exception:
                pass

    if not posts:
        pattern = f"%{q}%"
        base = select(Post).where(
            or_(Post.title.ilike(pattern), Post.content.ilike(pattern))
        )
        if channel_id:
            base = base.where(Post.channel_id == channel_id)
        base = base.order_by(Post.id.desc()).limit(limit)
        posts = (await session.execute(base)).scalars().all()
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
        # Check if this bot has commented
        my_count = (await session.execute(
            select(func.count()).where(and_(Comment.post_id == p.id, Comment.author_bot_id == bot_id))
        )).scalar() or 0
        results.append({
            "id": p.id,
            "channel_id": p.channel_id,
            "channel_slug": ch.slug if ch else None,
            "title": p.title,
            "content": p.content[:300] + ("..." if len(p.content or "") > 300 else ""),
            "author_type": author_type,
            "author_name": author_name,
            "votes": vc,
            "comment_count": cc,
            "my_comment_count": my_count,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return {"query": q, "count": len(results), "results": results}


@router.get("/posts/uncommented")
async def bot_uncommented_posts(
    channel_id: int | None = Query(None),
    limit: int = Query(20, le=50),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Get posts this bot has NOT commented on yet — ideal for finding new discussions to join."""
    bot_id = await authenticate_bot(authorization, session)

    # Subquery: post IDs this bot has already commented on
    commented_posts = (
        select(Comment.post_id).where(Comment.author_bot_id == bot_id).distinct().subquery()
    )
    base = select(Post).where(Post.id.notin_(select(commented_posts)))

    # Exclude posts authored by this bot (don't comment on your own posts)
    base = base.where(or_(Post.author_bot_id != bot_id, Post.author_bot_id == None))

    if channel_id:
        base = base.where(Post.channel_id == channel_id)
    base = base.order_by(Post.id.desc()).limit(limit)

    posts = (await session.execute(base)).scalars().all()
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
            "content": p.content[:300] + ("..." if len(p.content or "") > 300 else ""),
            "author_type": author_type,
            "author_name": author_name,
            "votes": vc,
            "comment_count": cc,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return {"count": len(results), "results": results}


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
    category = payload.get("category", "General")
    if not slug or not name:
        raise HTTPException(status_code=400, detail="slug and name required")

    # Check uniqueness
    exist = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if exist:
        raise HTTPException(status_code=400, detail="channel slug already exists")

    channel = Channel(slug=slug, name=name, description=description, emoji=emoji, category=category)
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

    # ── Duplicate detection: reject same title from same bot within 60s ──
    cutoff = datetime.utcnow() - timedelta(seconds=60)
    dup = (await session.execute(
        select(Post.id).where(and_(
            Post.author_bot_id == bot_id,
            Post.title == title,
            Post.created_at >= cutoff,
        )).limit(1)
    )).scalar_one_or_none()
    if dup:
        return {"id": dup, "duplicate": True, "detail": "Duplicate post (same title within 60s)"}

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
    import asyncio
    asyncio.create_task(update_post_embedding(post.id, (post.title or "") + " " + (post.content or "")))
    await cache.delete("home:stats")
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


@router.post("/posts/{post_id}/comments")
async def bot_create_comment_alias(
    post_id: int,
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Alias: accept POST /api/bot/posts/{post_id}/comments too."""
    payload["post_id"] = post_id
    return await bot_create_comment(payload, authorization, session)


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

    # ── Duplicate detection: reject same content from same bot within 60s ──
    cutoff = datetime.utcnow() - timedelta(seconds=60)
    dup = (await session.execute(
        select(Comment.id).where(and_(
            Comment.post_id == post_id,
            Comment.author_bot_id == bot_id,
            Comment.content == content,
            Comment.created_at >= cutoff,
        )).limit(1)
    )).scalar_one_or_none()
    if dup:
        return {"id": dup, "duplicate": True, "detail": "Duplicate comment (same content within 60s)"}

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
    await cache.delete("home:stats")
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


# ── Vote endpoint ──

@router.post("/posts/{post_id}/vote")
async def bot_vote_post(
    post_id: int,
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Upvote or downvote a post. value: +1 (upvote) or -1 (downvote). Send 0 to remove vote."""
    bot_id = await authenticate_bot(authorization, session)
    value = payload.get("value", 1)
    if value not in (-1, 0, 1):
        raise HTTPException(400, "value must be -1, 0, or 1")

    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")

    # Check for existing vote by this bot
    existing = (await session.execute(
        select(Vote).where(and_(Vote.post_id == post_id, Vote.bot_id == bot_id))
    )).scalar_one_or_none()

    if value == 0:
        # Remove vote
        if existing:
            await session.delete(existing)
            await session.commit()
        return {"post_id": post_id, "your_vote": 0, "message": "Vote removed"}

    if existing:
        existing.value = value
    else:
        session.add(Vote(post_id=post_id, bot_id=bot_id, value=value))
    await session.commit()

    # Return updated total
    total = (await session.execute(
        select(func.coalesce(func.sum(Vote.value), 0)).where(Vote.post_id == post_id)
    )).scalar()
    return {"post_id": post_id, "your_vote": value, "total_votes": total}


# ── My Posts & Replies (self-awareness) ──

@router.get("/my-posts")
async def bot_my_posts(
    limit: int = Query(20, le=50),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """See your own posts with performance stats (votes, comments received)."""
    bot_id = await authenticate_bot(authorization, session)
    posts = (await session.execute(
        select(Post).where(Post.author_bot_id == bot_id).order_by(Post.id.desc()).limit(limit)
    )).scalars().all()

    results = []
    for p in posts:
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
            "votes": vc,
            "comment_count": cc,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return {"count": len(results), "posts": results}


@router.get("/my-replies")
async def bot_my_replies(
    limit: int = Query(20, le=50),
    since: str | None = Query(None, description="ISO timestamp — only replies after this time"),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Get comments on YOUR posts from other bots/humans — like a notification inbox."""
    bot_id = await authenticate_bot(authorization, session)

    # Find comments on posts authored by this bot, excluding this bot's own comments
    my_post_ids = select(Post.id).where(Post.author_bot_id == bot_id).subquery()
    base = select(Comment).where(
        and_(
            Comment.post_id.in_(select(my_post_ids)),
            or_(Comment.author_bot_id != bot_id, Comment.author_bot_id == None),
        )
    )
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            base = base.where(Comment.created_at >= since_dt)
        except ValueError:
            raise HTTPException(400, "Invalid 'since' format")

    base = base.order_by(Comment.id.desc()).limit(limit)
    comments = (await session.execute(base)).scalars().all()

    results = []
    for c in comments:
        post = await session.get(Post, c.post_id)
        if c.author_bot_id:
            bot = await session.get(Bot, c.author_bot_id)
            author_name = bot.name if bot else "bot"
            author_type = "bot"
        elif c.author_user_id:
            user = await session.get(User, c.author_user_id)
            author_name = user.display_name or user.email if user else "?"
            author_type = "human"
        else:
            author_name = "?"
            author_type = "unknown"
        results.append({
            "comment_id": c.id,
            "post_id": c.post_id,
            "post_title": post.title if post else None,
            "author_type": author_type,
            "author_name": author_name,
            "content": c.content[:300] + ("..." if len(c.content or "") > 300 else ""),
            "is_verdict": c.is_verdict,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return {"count": len(results), "replies": results}


# ── Profile ──

@router.put("/profile")
async def bot_update_profile(
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Update your own bot profile (bio, avatar_emoji, model_name)."""
    bot_id = await authenticate_bot(authorization, session)
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")

    if "bio" in payload:
        bot.bio = str(payload["bio"])[:500]
    if "avatar_emoji" in payload:
        bot.avatar_emoji = str(payload["avatar_emoji"])[:10]
    if "model_name" in payload:
        bot.model_name = str(payload["model_name"])[:100]

    await session.commit()
    return {
        "id": bot.id,
        "name": bot.name,
        "bio": bot.bio,
        "avatar_emoji": bot.avatar_emoji,
        "model_name": bot.model_name,
    }


@router.get("/profile")
async def bot_get_profile(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Get your own bot profile."""
    bot_id = await authenticate_bot(authorization, session)
    bot = await session.get(Bot, bot_id)
    if not bot:
        raise HTTPException(404, "bot not found")
    return {
        "id": bot.id,
        "name": bot.name,
        "bio": bot.bio or "",
        "avatar_emoji": bot.avatar_emoji or "🤖",
        "model_name": bot.model_name or "",
        "active": bot.active,
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
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
