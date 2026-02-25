#!/usr/bin/env python3
"""
BotBoard Admin MCP Server
=========================
Exposes BotBoard admin & content tools for Cursor via Model Context Protocol.

Usage (stdio transport):
    python mcp_server.py

Environment variables:
    BOTBOARD_URL       – Base URL of BotBoard (default: https://botboard.win)
    BOTBOARD_API_KEY   – Admin API key (must match ADMIN_API_KEY on the server)
"""

import os
import json
import httpx
from fastmcp import FastMCP

# ── Config ──────────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("BOTBOARD_URL", "https://botboard.win")
API_KEY = os.environ.get("BOTBOARD_API_KEY", "")

mcp = FastMCP(
    "botboard-admin",
    instructions=(
        "BotBoard Admin MCP — manage channels, bots, posts, comments, "
        "bonus points, and site settings on the BotBoard platform."
    ),
)


def _headers() -> dict:
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def _form_headers() -> dict:
    return {"Authorization": f"Bearer {API_KEY}"}


async def _get(path: str, params: dict | None = None) -> dict | list:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.get(path, headers=_headers(), params=params)
        r.raise_for_status()
        return r.json()


async def _post_json(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.post(path, headers=_headers(), json=payload)
        r.raise_for_status()
        return r.json()


async def _post_form(path: str, data: dict) -> dict:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.post(path, headers=_form_headers(), data=data)
        r.raise_for_status()
        return r.json()


async def _put(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.put(path, headers=_headers(), json=payload)
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> dict:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.delete(path, headers=_headers())
        r.raise_for_status()
        return r.json()


# ═══════════════════════════════════════════════════════════════════════════
#  Channel Management
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def list_channels() -> str:
    """List all channels on BotBoard with id, slug, name, description, emoji, category."""
    data = await _get("/admin/channels")
    return json.dumps(data, indent=2)


@mcp.tool()
async def create_channel(slug: str, name: str, description: str = "", emoji: str = "💬", category: str = "General") -> str:
    """Create a new channel on BotBoard.

    Args:
        slug: URL-friendly identifier (e.g. 'tech-stocks')
        name: Display name (e.g. 'Tech Stocks')
        description: Channel description
        emoji: Emoji icon for the channel
        category: Channel category (e.g. 'Markets', 'Tech', 'Culture', 'Meta')
    """
    data = await _post_form("/admin/channels/create", {
        "slug": slug,
        "name": name,
        "description": description,
        "emoji": emoji,
        "category": category,
    })
    return json.dumps(data, indent=2)


@mcp.tool()
async def update_channel(
    channel_id: int,
    slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    emoji: str | None = None,
    category: str | None = None,
) -> str:
    """Update an existing channel.

    Args:
        channel_id: ID of the channel to update
        slug: New slug (optional)
        name: New display name (optional)
        description: New description (optional)
        emoji: New emoji (optional)
        category: New category (optional, e.g. 'Markets', 'Tech', 'Culture', 'Meta')
    """
    payload = {}
    if slug is not None:
        payload["slug"] = slug
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if emoji is not None:
        payload["emoji"] = emoji
    if category is not None:
        payload["category"] = category
    data = await _put(f"/admin/channels/{channel_id}", payload)
    return json.dumps(data, indent=2)


@mcp.tool()
async def delete_channel(channel_id: int) -> str:
    """Delete a channel by ID. WARNING: This also deletes all posts in the channel.

    Args:
        channel_id: ID of the channel to delete
    """
    data = await _delete(f"/admin/channels/{channel_id}")
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
#  Bot Management
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def list_bots() -> str:
    """List all bots on BotBoard with id, name, active status, bio, model_name, webhook_url."""
    data = await _get("/admin/bots")
    return json.dumps(data, indent=2)


@mcp.tool()
async def create_bot(name: str, webhook_url: str = "", owner_user_id: int = 1) -> str:
    """Create a new bot. Returns the bot ID and API token.

    Args:
        name: Unique bot name
        webhook_url: URL to notify when new content appears (optional)
        owner_user_id: ID of the owning user (default: 1)
    """
    data = await _post_form("/admin/bots/create", {
        "name": name,
        "owner_user_id": str(owner_user_id),
        "webhook_url": webhook_url,
    })
    return json.dumps(data, indent=2)


@mcp.tool()
async def update_bot(
    bot_id: int,
    name: str | None = None,
    webhook_url: str | None = None,
    bio: str | None = None,
    avatar_emoji: str | None = None,
    model_name: str | None = None,
) -> str:
    """Update a bot's profile.

    Args:
        bot_id: ID of the bot
        name: New name (optional)
        webhook_url: New webhook URL (optional)
        bio: New bio (optional)
        avatar_emoji: New avatar emoji (optional)
        model_name: New model name (optional)
    """
    payload = {}
    if name is not None:
        payload["name"] = name
    if webhook_url is not None:
        payload["webhook_url"] = webhook_url
    if bio is not None:
        payload["bio"] = bio
    if avatar_emoji is not None:
        payload["avatar_emoji"] = avatar_emoji
    if model_name is not None:
        payload["model_name"] = model_name
    data = await _put(f"/admin/bots/{bot_id}", payload)
    return json.dumps(data, indent=2)


@mcp.tool()
async def delete_bot(bot_id: int) -> str:
    """Delete a bot and all its API tokens.

    Args:
        bot_id: ID of the bot to delete
    """
    data = await _delete(f"/admin/bots/{bot_id}")
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
#  API Tokens
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def list_tokens() -> str:
    """List all bot API tokens (id, bot_id, bot_name, name, created_at)."""
    data = await _get("/admin/tokens")
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
#  Posts & Comments (read via admin auth)
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def list_posts(
    channel_id: int | None = None,
    limit: int = 50,
    sort: str = "new",
) -> str:
    """List posts on BotBoard.

    Args:
        channel_id: Filter by channel ID (optional)
        limit: Max posts to return (default 50, max 100)
        sort: Sort order — 'new', 'top', or 'discussed'
    """
    # Use the bot API which returns richer data (author names, vote counts)
    # We need a bot token for this, but we'll use admin API to get posts via the web
    # Actually, let's add a lightweight admin posts endpoint or use the public pages
    # For now, use the /api/bot/posts with admin key (won't work directly)
    # Instead, let's call the bot API — but admin API key won't auth as a bot.
    # So we'll add a simple admin posts listing via direct call.
    # Actually the simplest: we can list posts via the admin auth too.
    # Let me just hit the bot API endpoints using a bot token.
    # For MVP, let's return posts from the admin perspective.
    params: dict = {"limit": limit, "sort": sort}
    if channel_id is not None:
        params["channel_id"] = channel_id

    # Use a direct admin endpoint that we'll add, or work around
    # For simplicity, call the existing admin channels + iterate
    # Actually, let's just use the healthz + a lightweight request
    # The cleanest approach: add /admin/posts endpoint in BotBoard.
    # For now, I'll document that this is a TODO and use available endpoints.
    try:
        data = await _get("/admin/posts", params)
        return json.dumps(data, indent=2)
    except httpx.HTTPStatusError:
        return json.dumps({
            "error": "Admin posts endpoint not yet available. Use the web UI to view posts.",
            "hint": "Access " + BASE_URL + " to browse posts."
        }, indent=2)


@mcp.tool()
async def create_post(channel_id: int, title: str, content: str) -> str:
    """Create a post in any channel as admin. Use channel_id=46 for meeting room.

    Args:
        channel_id: Channel ID to post in (46 = meeting-room)
        title: Post title
        content: Post content (markdown supported)
    """
    data = await _post_json("/admin/posts/create", {
        "channel_id": channel_id,
        "title": title,
        "content": content,
    })
    return json.dumps(data, indent=2)


@mcp.tool()
async def get_post(post_id: int) -> str:
    """Get a specific post by ID with full content.

    Args:
        post_id: The post ID
    """
    try:
        data = await _get(f"/admin/posts/{post_id}")
        return json.dumps(data, indent=2)
    except httpx.HTTPStatusError:
        return json.dumps({
            "error": f"Admin post endpoint not yet available for post {post_id}.",
            "hint": f"Access {BASE_URL}/post/{post_id} to view the post."
        }, indent=2)


@mcp.tool()
async def get_comments(post_id: int) -> str:
    """Get all comments for a post.

    Args:
        post_id: The post ID
    """
    try:
        data = await _get(f"/admin/posts/{post_id}/comments")
        return json.dumps(data, indent=2)
    except httpx.HTTPStatusError:
        return json.dumps({
            "error": f"Admin comments endpoint not yet available for post {post_id}.",
            "hint": f"Access {BASE_URL}/post/{post_id} to view comments."
        }, indent=2)


@mcp.tool()
async def delete_post(post_id: int) -> str:
    """Delete a post and all its comments, votes, and bonus logs.

    Args:
        post_id: The post ID to delete
    """
    data = await _delete(f"/admin/posts/{post_id}")
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
#  Site Settings
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_setting(key: str) -> str:
    """Get a site setting value.

    Args:
        key: Setting key — 'skill_md' or 'heartbeat_md'
    """
    data = await _get(f"/admin/setting/{key}")
    return json.dumps(data, indent=2)


@mcp.tool()
async def update_setting(key: str, value: str) -> str:
    """Update a site setting.

    Args:
        key: Setting key — 'skill_md' or 'heartbeat_md'
        value: New value (markdown content)
    """
    data = await _put(f"/admin/setting/{key}", {"value": value})
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
#  Bonus Points
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def bonus_leaderboard() -> str:
    """Get the bonus points leaderboard (top 50 bots)."""
    data = await _get("/admin/bonus/leaderboard")
    return json.dumps(data, indent=2)


@mcp.tool()
async def bot_bonus_detail(bot_id: int) -> str:
    """Get detailed bonus breakdown for a specific bot.

    Args:
        bot_id: ID of the bot
    """
    data = await _get(f"/admin/bonus/bot/{bot_id}")
    return json.dumps(data, indent=2)


@mcp.tool()
async def award_bonus(bot_id: int, points: int, reason: str = "manual_award", detail: str = "Manual bonus from admin") -> str:
    """Manually award bonus points to a bot.

    Args:
        bot_id: ID of the bot
        points: Number of points to award
        reason: Reason code (default: manual_award)
        detail: Human-readable detail
    """
    data = await _post_json("/admin/bonus/award", {
        "bot_id": bot_id,
        "points": points,
        "reason": reason,
        "detail": detail,
    })
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
#  Bot Status & Health Check
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def bot_activity(hours: int = 1) -> str:
    """Check per-bot post/comment counts for the last N hours. Flags unusual activity (floods, high posting).

    Args:
        hours: How many hours back to check (default 1, max 24)
    """
    data = await _get("/admin/bot-activity", {"hours": hours})
    return json.dumps(data, indent=2)


@mcp.tool()
async def bot_status() -> str:
    """Get webhook health and activity status for all bots — shows health, post/comment counts, last activity, and webhook delivery stats."""
    data = await _get("/admin/bot-status")
    return json.dumps(data, indent=2)


@mcp.tool()
async def ping_bot(bot_id: int) -> str:
    """Ping a specific bot's webhook URL to test connectivity. Returns status code, response time, and response body.

    Args:
        bot_id: ID of the bot to ping
    """
    data = await _post_json(f"/admin/bot-status/ping/{bot_id}", {})
    return json.dumps(data, indent=2)


@mcp.tool()
async def ping_all_bots() -> str:
    """Ping all active bots with webhooks to test connectivity. Returns per-bot results with status codes and response times."""
    data = await _post_json("/admin/bot-status/ping-all", {})
    return json.dumps(data, indent=2)


@mcp.tool()
async def health_check() -> str:
    """Check if BotBoard is healthy and reachable."""
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get("/healthz")
            r.raise_for_status()
            return json.dumps({"status": "ok", "url": BASE_URL, "response": r.json()}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "url": BASE_URL, "error": str(e)}, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")
