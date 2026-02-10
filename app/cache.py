"""Simple Redis cache for hot queries.

Usage:
    from app.cache import cache

    # Get or set
    data = await cache.get("channels")
    if data is None:
        data = fetch_from_db()
        await cache.set("channels", data, ttl=60)

    # Invalidate
    await cache.delete("channels")
"""

import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore


class RedisCache:
    """Thin async Redis wrapper with JSON serialisation and graceful fallback."""

    def __init__(self) -> None:
        self._pool: Any = None

    async def _get_pool(self):
        if self._pool is not None:
            return self._pool
        if not settings.REDIS_URL or aioredis is None:
            return None
        try:
            self._pool = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            # Quick ping to verify
            await self._pool.ping()
            logger.info("Redis cache connected: %s", settings.REDIS_URL)
            return self._pool
        except Exception as exc:
            logger.warning("Redis unavailable, caching disabled: %s", exc)
            self._pool = None
            return None

    async def get(self, key: str) -> Any | None:
        pool = await self._get_pool()
        if pool is None:
            return None
        try:
            raw = await pool.get(f"bb:{key}")
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: int = 60) -> None:
        pool = await self._get_pool()
        if pool is None:
            return
        try:
            await pool.set(f"bb:{key}", json.dumps(value, default=str), ex=ttl)
        except Exception:
            pass

    async def delete(self, *keys: str) -> None:
        pool = await self._get_pool()
        if pool is None:
            return
        try:
            await pool.delete(*(f"bb:{k}" for k in keys))
        except Exception:
            pass


cache = RedisCache()
