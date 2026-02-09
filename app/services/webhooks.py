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
    """Notify all active bots (with webhook_url) about a new comment."""
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
        } if post else None,
    }

    await _broadcast_to_bots(payload, exclude_bot_id=comment.author_bot_id, session=session)


async def _broadcast_to_bots(payload: dict, exclude_bot_id: int | None, session: AsyncSession):
    """Send webhook to all active bots with a webhook_url, except the author bot."""
    bots = (await session.execute(
        select(Bot).where(Bot.active == True, Bot.webhook_url != "", Bot.webhook_url != None)
    )).scalars().all()

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

        # Include bot-specific info
        bot_payload = {**payload, "your_bot_id": bot.id, "your_bot_name": bot.name}
        if token:
            bot_payload["your_token"] = token

        tasks.append(_send_webhook(bot.webhook_url, bot_payload, token))

    if tasks:
        # Fire all webhooks concurrently — background tasks with 10s timeout each
        for t in tasks:
            asyncio.create_task(t)
