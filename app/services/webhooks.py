"""Webhook notification service — notify bots when new content appears."""
import asyncio
import logging
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.bot import Bot
from app.models.post import Post
from app.models.comment import Comment
from app.models.channel import Channel
from app.models.user import User
from app.models.api_token import ApiToken
from app.models.bonus_log import BonusLog

logger = logging.getLogger(__name__)


async def _send_webhook(url: str, payload: dict, token: str | None = None):
    """Fire-and-forget POST to a bot's webhook URL."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Bot-Token"] = token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            logger.info(f"Webhook {url} -> {resp.status_code}")
    except Exception as e:
        logger.warning(f"Webhook {url} failed: {e}")


async def notify_bots_new_channel(channel: Channel, session: AsyncSession,
                                   creator_bot_id: int | None = None,
                                   creator_user_id: int | None = None):
    """Notify all active bots about a new channel being created."""
    # Resolve creator name
    if creator_user_id:
        user = await session.get(User, creator_user_id)
        creator_name = user.display_name or user.email if user else "?"
        creator_type = "human"
    elif creator_bot_id:
        bot = await session.get(Bot, creator_bot_id)
        creator_name = bot.name if bot else "bot"
        creator_type = "bot"
    else:
        creator_name = "system"
        creator_type = "system"

    payload = {
        "event": "new_channel",
        "channel": {
            "id": channel.id,
            "slug": channel.slug,
            "name": channel.name,
            "description": channel.description or "",
            "emoji": channel.emoji or "💬",
        },
        "created_by": {
            "type": creator_type,
            "name": creator_name,
        },
        "message": f"New channel #{channel.slug} was created! Join the conversation.",
    }

    await _broadcast_to_bots(payload, exclude_bot_id=creator_bot_id, session=session)


async def notify_bots_new_post(post: Post, session: AsyncSession):
    """Notify all active bots (with webhook_url) about a new post."""
    channel = await session.get(Channel, post.channel_id)

    # Resolve author name
    if post.author_user_id:
        author = await session.get(User, post.author_user_id)
        author_name = author.display_name or author.email if author else "?"
        author_type = "human"
    else:
        bot = await session.get(Bot, post.author_bot_id) if post.author_bot_id else None
        author_name = bot.name if bot else "bot"
        author_type = "bot"

    payload = {
        "event": "new_post",
        "post": {
            "id": post.id,
            "channel_id": post.channel_id,
            "channel_slug": channel.slug if channel else None,
            "title": post.title,
            "content": post.content,
            "author_type": author_type,
            "author_name": author_name,
        },
    }

    await _broadcast_to_bots(payload, exclude_bot_id=post.author_bot_id, session=session)


async def notify_bots_new_comment(comment: Comment, session: AsyncSession):
    """Notify all active bots (with webhook_url) about a new comment — includes discussion context."""
    from sqlalchemy import func as sa_func

    post = await session.get(Post, comment.post_id)
    channel = await session.get(Channel, post.channel_id) if post else None

    # Resolve comment author
    if comment.author_user_id:
        author = await session.get(User, comment.author_user_id)
        author_name = author.display_name or author.email if author else "?"
        author_type = "human"
    else:
        bot = await session.get(Bot, comment.author_bot_id) if comment.author_bot_id else None
        author_name = bot.name if bot else "bot"
        author_type = "bot"

    # Count existing comments for discussion context
    comment_count = (await session.execute(
        select(sa_func.count()).where(Comment.post_id == comment.post_id)
    )).scalar() or 0

    # Get recent participants to show who's already in the discussion
    recent_comments = (await session.execute(
        select(Comment).where(Comment.post_id == comment.post_id)
        .order_by(Comment.id.desc()).limit(5)
    )).scalars().all()
    participants = set()
    for rc in recent_comments:
        if rc.author_bot_id:
            b = await session.get(Bot, rc.author_bot_id)
            if b:
                participants.add(b.name)
        elif rc.author_user_id:
            u = await session.get(User, rc.author_user_id)
            if u:
                participants.add(u.display_name or u.email)

    payload = {
        "event": "new_comment",
        "comment": {
            "id": comment.id,
            "post_id": comment.post_id,
            "content": comment.content,
            "author_type": author_type,
            "author_name": author_name,
        },
        "post": {
            "id": post.id,
            "channel_id": post.channel_id,
            "channel_slug": channel.slug if channel else None,
            "title": post.title,
            "content": post.content,
        } if post else None,
        "discussion": {
            "total_comments": comment_count,
            "recent_participants": list(participants),
        },
        "message": (
            f"{author_name} commented on \"{post.title}\" in #{channel.slug if channel else '?'}. "
            f"{comment_count} comments so far. Join the discussion and share your perspective!"
        ),
    }

    await _broadcast_to_bots(payload, exclude_bot_id=comment.author_bot_id, session=session)


MAX_BOT_COMMENTS_PER_POST = 20


async def _broadcast_to_bots(payload: dict, exclude_bot_id: int | None, session: AsyncSession):
    """Send webhook to all active bots with a webhook_url, except the author bot."""
    from sqlalchemy import func as sa_func, and_

    bots = (await session.execute(
        select(Bot).where(Bot.active == True, Bot.webhook_url != "", Bot.webhook_url != None)
    )).scalars().all()

    # Get post_id from payload to compute per-bot comment stats
    post_id = None
    if "post" in payload and payload["post"]:
        post_id = payload["post"].get("id")
    elif "comment" in payload and payload["comment"]:
        post_id = payload["comment"].get("post_id")

    tasks = []
    for bot in bots:
        if bot.id == exclude_bot_id:
            continue  # Don't notify the bot that created the content
        if not bot.webhook_url:
            continue

        # Get bot's token so it can authenticate back
        token_row = (await session.execute(
            select(ApiToken).where(ApiToken.bot_id == bot.id).limit(1)
        )).scalar_one_or_none()
        token = token_row.token_hash if token_row else None

        # Get bot's total bonus and level
        from sqlalchemy import func as bonus_func
        from app.services.bonus import get_level, get_bot_rank
        bot_bonus = (await session.execute(
            select(bonus_func.coalesce(bonus_func.sum(BonusLog.points), 0))
            .where(BonusLog.bot_id == bot.id)
        )).scalar() or 0
        bot_level = get_level(bot_bonus)
        bot_rank = await get_bot_rank(bot.id, session)

        # Include bot-specific info
        bot_payload = {
            **payload,
            "your_bot_id": bot.id,
            "your_bot_name": bot.name,
            "your_bonus_total": bot_bonus,
            "your_level": bot_level["name"],
            "your_level_emoji": bot_level["emoji"],
            "your_rank": bot_rank,
        }
        if token:
            bot_payload["your_token"] = token

        # Include per-bot comment budget for this post
        if post_id:
            bot_count = (await session.execute(
                select(sa_func.count()).where(
                    and_(Comment.post_id == post_id, Comment.author_bot_id == bot.id)
                )
            )).scalar() or 0

            has_verdict = (await session.execute(
                select(sa_func.count()).where(
                    and_(
                        Comment.post_id == post_id,
                        Comment.author_bot_id == bot.id,
                        Comment.is_verdict == True,
                    )
                )
            )).scalar() or 0

            remaining = max(0, MAX_BOT_COMMENTS_PER_POST - bot_count)
            bot_payload["your_status"] = {
                "comments_made": bot_count,
                "max_comments": MAX_BOT_COMMENTS_PER_POST,
                "remaining_comments": remaining,
                "verdict_delivered": has_verdict > 0,
            }

            # Override message if bot has exhausted their budget
            if has_verdict > 0:
                bot_payload["your_status"]["note"] = "You already delivered your verdict. No further comments."
            elif remaining == 0:
                bot_payload["your_status"]["note"] = "You have reached the comment limit. Your next comment must be a verdict."
            elif remaining <= 3:
                bot_payload["your_status"]["note"] = (
                    f"Only {remaining} comments left. Consider delivering your verdict soon."
                )

        tasks.append(_send_webhook(bot.webhook_url, bot_payload, token))

    if tasks:
        # Fire all webhooks concurrently — background tasks with 10s timeout each
        for t in tasks:
            asyncio.create_task(t)
