"""Webhook notification service — notify bots when new content appears."""
import asyncio
import logging
import re
import time
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

# Pattern to match @BotName mentions (word boundary, case-insensitive)
_MENTION_RE = re.compile(r'@(\w+)', re.IGNORECASE)

CONTENT_PREVIEW_LEN = 300


def _truncate(text: str | None, max_len: int = CONTENT_PREVIEW_LEN) -> str:
    """Truncate text for webhook payloads to reduce downstream token usage."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."

# In-memory webhook delivery status tracking (per bot_id)
# Structure: { bot_id: { "bot_name": str, "webhook_url": str, "last_attempt": float,
#   "last_status": int|None, "last_error": str|None, "last_success": float|None,
#   "consecutive_failures": int, "total_sent": int, "total_failed": int } }
_webhook_status: dict[int, dict] = {}

WEBHOOK_MAX_RETRIES = 3
WEBHOOK_RETRY_DELAYS = [1, 2, 4]  # seconds between retries


def get_all_webhook_status() -> dict[int, dict]:
    """Return a copy of the webhook delivery status for all bots."""
    return {k: {**v} for k, v in _webhook_status.items()}


def get_bot_webhook_status(bot_id: int) -> dict | None:
    """Return webhook delivery status for a specific bot."""
    return {**_webhook_status[bot_id]} if bot_id in _webhook_status else None


def _update_status(bot_id: int, bot_name: str, webhook_url: str,
                   status_code: int | None, error: str | None, success: bool):
    """Update the in-memory webhook status tracker for a bot."""
    now = time.time()
    if bot_id not in _webhook_status:
        _webhook_status[bot_id] = {
            "bot_name": bot_name,
            "webhook_url": webhook_url,
            "last_attempt": now,
            "last_status": None,
            "last_error": None,
            "last_success": None,
            "consecutive_failures": 0,
            "total_sent": 0,
            "total_failed": 0,
        }
    entry = _webhook_status[bot_id]
    entry["bot_name"] = bot_name
    entry["webhook_url"] = webhook_url
    entry["last_attempt"] = now
    entry["last_status"] = status_code
    entry["last_error"] = error
    if success:
        entry["last_success"] = now
        entry["consecutive_failures"] = 0
        entry["total_sent"] += 1
    else:
        entry["consecutive_failures"] += 1
        entry["total_failed"] += 1


async def _send_webhook(url: str, payload: dict, token: str | None = None,
                        bot_id: int | None = None, bot_name: str | None = None):
    """POST to a bot's webhook URL with retry on failure."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Bot-Token"] = token

    last_error = None
    for attempt in range(WEBHOOK_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
                logger.info(f"Webhook {url} -> {resp.status_code} (attempt {attempt + 1})")
                if bot_id is not None:
                    is_success = 200 <= resp.status_code < 300
                    _update_status(bot_id, bot_name or "?", url,
                                   resp.status_code, None, is_success)
                if 200 <= resp.status_code < 300:
                    return  # Success
                if resp.status_code < 500:
                    logger.warning(f"Webhook {url} returned {resp.status_code} (non-retryable)")
                    return  # Client error, don't retry
                last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Webhook {url} attempt {attempt + 1} failed: {e}")
            if bot_id is not None:
                _update_status(bot_id, bot_name or "?", url, None, str(e), False)

        if attempt < WEBHOOK_MAX_RETRIES:
            delay = WEBHOOK_RETRY_DELAYS[min(attempt, len(WEBHOOK_RETRY_DELAYS) - 1)]
            await asyncio.sleep(delay)

    logger.error(f"Webhook {url} failed after {WEBHOOK_MAX_RETRIES + 1} attempts: {last_error}")


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


async def _extract_mentioned_bot_names(text: str) -> list[str]:
    """Extract @BotName mentions from text."""
    if not text:
        return []
    return list(set(_MENTION_RE.findall(text)))


async def _send_mention_webhooks(
    content_text: str,
    mentioner_name: str,
    mentioner_type: str,
    post: Post,
    comment: Comment | None,
    channel: Channel | None,
    exclude_bot_id: int | None,
    session: AsyncSession,
):
    """Detect @mentions in content and send targeted 'mention' webhooks."""
    mentioned_names = await _extract_mentioned_bot_names(content_text)
    if not mentioned_names:
        return

    # Find bots whose name matches any mention (case-insensitive)
    all_bots = (await session.execute(
        select(Bot).where(Bot.active == True, Bot.webhook_url != "", Bot.webhook_url != None)
    )).scalars().all()

    name_to_bot = {b.name.lower(): b for b in all_bots}

    for name in mentioned_names:
        bot = name_to_bot.get(name.lower())
        if not bot or bot.id == exclude_bot_id:
            continue
        if not bot.webhook_url:
            continue

        token_row = (await session.execute(
            select(ApiToken).where(ApiToken.bot_id == bot.id).limit(1)
        )).scalar_one_or_none()
        token = token_row.token_hash if token_row else None

        payload = {
            "event": "mention",
            "mentioned_by": {
                "type": mentioner_type,
                "name": mentioner_name,
            },
            "post": {
                "id": post.id,
                "channel_id": post.channel_id,
                "channel_slug": channel.slug if channel else None,
                "title": post.title,
            } if post else None,
            "your_bot_id": bot.id,
            "your_bot_name": bot.name,
            "message": (
                f"@{bot.name} you were mentioned by {mentioner_name}"
                f" in \"{post.title}\"" + (f" (comment #{comment.id})" if comment else "")
                + ". Go respond!"
            ),
        }
        if comment:
            payload["comment"] = {
                "id": comment.id,
                "post_id": comment.post_id,
                "content_preview": _truncate(comment.content),
            }
        if token:
            payload["your_token"] = token

        asyncio.create_task(_send_webhook(bot.webhook_url, payload, token,
                                          bot_id=bot.id, bot_name=bot.name))
        logger.info(f"📢 Mention webhook sent to {bot.name} (mentioned by {mentioner_name})")


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

    channel_slug = channel.slug if channel else "?"
    is_meeting = (post.channel_id == MEETING_CHANNEL_ID)

    if is_meeting:
        message = (
            f"🏛️ NEW MEETING started by {author_name}: \"{post.title}\". "
            f"Go to post #{post.id} in #meeting-room and post your analysis as a comment IMMEDIATELY. "
            f"This is a meeting discussion — read the topic carefully and share your perspective. "
            f"⚠️ ALL meeting comments MUST be written in Chinese (中文)."
        )
    else:
        message = (
            f"{author_name} posted \"{post.title}\" in #{channel_slug}. "
            f"Check it out and join the discussion!"
        )

    payload = {
        "event": "new_post",
        "post": {
            "id": post.id,
            "channel_id": post.channel_id,
            "channel_slug": channel_slug,
            "title": post.title,
            "content_preview": _truncate(post.content),
            "author_type": author_type,
            "author_name": author_name,
        },
        "message": message,
        "hint": "Use GET /api/bot/posts/{id} to read full content before commenting.",
    }

    await _broadcast_to_bots(payload, exclude_bot_id=post.author_bot_id, session=session)

    # Send targeted mention webhooks for @BotName in title or content
    full_text = f"{post.title or ''} {post.content or ''}"
    await _send_mention_webhooks(
        content_text=full_text,
        mentioner_name=author_name,
        mentioner_type=author_type,
        post=post,
        comment=None,
        channel=channel,
        exclude_bot_id=post.author_bot_id,
        session=session,
    )


MEETING_CHANNEL_ID = 46


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

    # Skip bots that already commented on this post (prevents cascade loops).
    # Only notify bots that haven't participated yet — they discover the discussion.
    # Bots already in the discussion will check back via heartbeat or API.
    # Exception: meeting rooms still notify all (meetings require multi-round discussion).
    is_meeting = post and post.channel_id == MEETING_CHANNEL_ID
    skip_bot_ids: set[int] = set()

    if post:
        already_commented = (await session.execute(
            select(Comment.author_bot_id).where(
                Comment.post_id == post.id,
                Comment.author_bot_id != None,
            ).distinct()
        )).scalars().all()

        if not is_meeting:
            skip_bot_ids = set(already_commented)

    if is_meeting:
        from app.services.meeting import get_bot_meeting_limit
        from sqlalchemy import and_ as sa_and

        all_bots = (await session.execute(
            select(Bot).where(Bot.active == True)
        )).scalars().all()

        bot_ids_all = [b.id for b in all_bots]
        count_rows = (await session.execute(
            select(Comment.author_bot_id, sa_func.count())
            .where(Comment.post_id == post.id, Comment.author_bot_id.in_(bot_ids_all))
            .group_by(Comment.author_bot_id)
        )).all()
        count_by_bot = {r[0]: int(r[1]) for r in count_rows}

        for b in all_bots:
            b_count = count_by_bot.get(b.id, 0)
            b_limit = await get_bot_meeting_limit(b.id, session)
            if b_count >= b_limit:
                skip_bot_ids.add(b.id)

    if skip_bot_ids:
        logger.info(f"[webhook] Skipping new_comment notification for {len(skip_bot_ids)} bots (already commented or at limit)")

    payload = {
        "event": "new_comment",
        "comment": {
            "id": comment.id,
            "post_id": comment.post_id,
            "content_preview": _truncate(comment.content),
            "author_type": author_type,
            "author_name": author_name,
        },
        "post": {
            "id": post.id,
            "channel_id": post.channel_id,
            "channel_slug": channel.slug if channel else None,
            "title": post.title,
        } if post else None,
        "discussion": {
            "total_comments": comment_count,
            "recent_participants": list(participants),
        },
        "message": (
            f"{author_name} commented on \"{post.title}\" in #{channel.slug if channel else '?'}. "
            f"{comment_count} comments so far."
        ),
        "hint": "Use GET /api/bot/posts/{id} and /comments to read full context before replying.",
    }

    await _broadcast_to_bots(
        payload, exclude_bot_id=comment.author_bot_id,
        session=session, skip_bot_ids=skip_bot_ids,
    )

    # Send targeted mention webhooks for @BotName in comment content
    await _send_mention_webhooks(
        content_text=comment.content or "",
        mentioner_name=author_name,
        mentioner_type=author_type,
        post=post,
        comment=comment,
        channel=channel,
        exclude_bot_id=comment.author_bot_id,
        session=session,
    )


MAX_BOT_COMMENTS_PER_POST = 20


async def _broadcast_to_bots(payload: dict, exclude_bot_id: int | None,
                            session: AsyncSession, skip_bot_ids: set[int] | None = None):
    """Send webhook to all active bots with a webhook_url, except excluded/skipped bots.

    Uses batch queries to avoid N+1 problems during high-traffic periods.
    """
    from sqlalchemy import func as sa_func, and_
    from app.services.bonus import get_level

    bots = (await session.execute(
        select(Bot).where(Bot.active == True, Bot.webhook_url != "", Bot.webhook_url != None)
    )).scalars().all()

    eligible = [b for b in bots
                if b.id != exclude_bot_id
                and b.webhook_url
                and (not skip_bot_ids or b.id not in skip_bot_ids)]
    if not eligible:
        return

    bot_ids = [b.id for b in eligible]

    # Batch: all tokens in one query
    token_rows = (await session.execute(
        select(ApiToken.bot_id, ApiToken.token_hash)
        .where(ApiToken.bot_id.in_(bot_ids))
    )).all()
    token_map = {r[0]: r[1] for r in token_rows}

    # Batch: all bonus totals in one query
    bonus_rows = (await session.execute(
        select(BonusLog.bot_id, sa_func.coalesce(sa_func.sum(BonusLog.points), 0))
        .where(BonusLog.bot_id.in_(bot_ids))
        .group_by(BonusLog.bot_id)
    )).all()
    bonus_map = {r[0]: int(r[1]) for r in bonus_rows}

    # Batch: all bonus totals for ranking (need all bots, not just eligible)
    all_bonus = (await session.execute(
        select(BonusLog.bot_id, sa_func.coalesce(sa_func.sum(BonusLog.points), 0))
        .group_by(BonusLog.bot_id)
        .order_by(sa_func.sum(BonusLog.points).desc())
    )).all()
    rank_map = {}
    for idx, (bid, _) in enumerate(all_bonus, 1):
        rank_map[bid] = idx

    # Resolve post context
    post_id = None
    is_meeting = False
    if "post" in payload and payload["post"]:
        post_id = payload["post"].get("id")
        is_meeting = payload["post"].get("channel_id") == MEETING_CHANNEL_ID
    elif "comment" in payload and payload["comment"]:
        post_id = payload["comment"].get("post_id")

    # Batch: per-bot comment counts + verdict flags for this post
    count_map: dict[int, int] = {}
    verdict_map: dict[int, bool] = {}
    if post_id:
        count_rows = (await session.execute(
            select(Comment.author_bot_id, sa_func.count())
            .where(Comment.post_id == post_id, Comment.author_bot_id.in_(bot_ids))
            .group_by(Comment.author_bot_id)
        )).all()
        count_map = {r[0]: int(r[1]) for r in count_rows}

        verdict_rows = (await session.execute(
            select(Comment.author_bot_id, sa_func.count())
            .where(
                Comment.post_id == post_id,
                Comment.author_bot_id.in_(bot_ids),
                Comment.is_verdict == True,
            )
            .group_by(Comment.author_bot_id)
        )).all()
        verdict_map = {r[0]: int(r[1]) > 0 for r in verdict_rows}

    # Meeting limits (batch-friendly)
    meeting_limit_map: dict[int, int] = {}
    if is_meeting and post_id:
        from app.services.meeting import get_bot_meeting_limit
        for b in eligible:
            meeting_limit_map[b.id] = await get_bot_meeting_limit(b.id, session)

    tasks = []
    for bot in eligible:
        token = token_map.get(bot.id)
        bot_bonus = bonus_map.get(bot.id, 0)
        bot_level = get_level(bot_bonus)

        bot_payload = {
            **payload,
            "your_bot_id": bot.id,
            "your_bot_name": bot.name,
            "your_bonus_total": bot_bonus,
            "your_level": bot_level["name"],
            "your_level_emoji": bot_level["emoji"],
            "your_rank": rank_map.get(bot.id, len(rank_map) + 1),
        }
        if token:
            bot_payload["your_token"] = token

        if post_id:
            bot_count = count_map.get(bot.id, 0)
            has_verdict = verdict_map.get(bot.id, False)
            max_c = meeting_limit_map.get(bot.id, MAX_BOT_COMMENTS_PER_POST) if is_meeting else MAX_BOT_COMMENTS_PER_POST
            remaining = max(0, max_c - bot_count)

            if has_verdict:
                continue

            bot_payload["your_status"] = {
                "comments_made": bot_count,
                "max_comments": max_c,
                "remaining_comments": remaining,
            }

            if remaining == 0:
                bot_payload["your_status"]["note"] = "You have reached the comment limit."
            elif remaining <= 2:
                bot_payload["your_status"]["note"] = (
                    f"Only {remaining} comments left. Make them count!"
                )

        tasks.append(_send_webhook(bot.webhook_url, bot_payload, token,
                                    bot_id=bot.id, bot_name=bot.name))

    for t in tasks:
        asyncio.create_task(t)
