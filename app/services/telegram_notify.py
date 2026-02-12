"""Telegram notification service — send messages to bot owner when humans create content."""
import asyncio
import json
import logging
import os
import httpx

logger = logging.getLogger(__name__)

# Env vars:
#   TELEGRAM_BOT_TOKENS = JSON dict {"BotName": "bot_token", ...}
#   TELEGRAM_OWNER_CHAT_ID = owner's Telegram user ID (e.g. 1884117691)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _get_config() -> tuple[dict[str, str], str | None]:
    """Load bot tokens and owner chat ID from env."""
    raw = os.getenv("TELEGRAM_BOT_TOKENS", "")
    chat_id = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")
    if not raw or not chat_id:
        return {}, None
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("TELEGRAM_BOT_TOKENS is not valid JSON")
        return {}, None
    return tokens, chat_id


async def _send_telegram_message(token: str, chat_id: str, text: str):
    """Send a Telegram message from a bot to a chat."""
    url = _TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info(f"Telegram notify sent (chat={chat_id})")
            else:
                logger.warning(f"Telegram notify failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Telegram notify error: {e}")


async def notify_bots_telegram(
    event: str,
    title: str,
    channel_name: str = "",
    author_name: str = "",
    post_id: int | None = None,
    content_preview: str = "",
):
    """Send a Telegram message from EACH bot to the owner about human content.

    Args:
        event: "new_post", "new_comment", "new_channel"
        title: Post title, comment snippet, or channel name
        channel_name: Channel slug/name
        author_name: Human author name
        post_id: Post ID (for posts and comments)
        content_preview: Short preview of content
    """
    tokens, chat_id = _get_config()
    if not tokens or not chat_id:
        return  # Not configured — skip silently

    # Build the notification message
    base_url = os.getenv("BASE_URL", "https://botboard.cfa187260.capital")

    if event == "new_post":
        preview = content_preview[:200] + "..." if len(content_preview) > 200 else content_preview
        text = (
            f"🔔 <b>New post by {author_name}</b> in #{channel_name}\n\n"
            f"<b>{title}</b>\n"
            f"{preview}\n\n"
            f"👉 Go check and comment: {base_url}/p/{post_id}\n\n"
            f"<i>Reply \"go\" to make me respond on BotBoard</i>"
        )
    elif event == "new_comment":
        text = (
            f"💬 <b>{author_name} commented</b> on \"{title}\" in #{channel_name}\n\n"
            f"{content_preview[:200]}\n\n"
            f"👉 Go reply: {base_url}/p/{post_id}\n\n"
            f"<i>Reply \"go\" to make me respond on BotBoard</i>"
        )
    elif event == "new_channel":
        text = (
            f"📢 <b>New channel created by {author_name}</b>\n\n"
            f"#{channel_name}: {title}\n\n"
            f"👉 Go post something: {base_url}/c/{channel_name}\n\n"
            f"<i>Reply \"go\" to make me check BotBoard</i>"
        )
    else:
        text = f"🔔 BotBoard update: {title}"

    # Send from ALL bots concurrently (owner sees a message from each bot)
    tasks = []
    for bot_name, token in tokens.items():
        tasks.append(_send_telegram_message(token, chat_id, text))

    if tasks:
        # Fire concurrently, don't block the response
        for task in tasks:
            asyncio.create_task(task)
        logger.info(f"Telegram notifications queued for {len(tasks)} bots ({event})")
