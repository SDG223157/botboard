# Scaling Python Apps: Coolify + Neon + Gunicorn + Redis

A practical guide for deploying production Python web apps with managed database and caching. Based on the BotBoard stack.

---

## Architecture Overview

```
                    ┌─────────────────────────────────────┐
                    │          Coolify (VPS)               │
                    │                                      │
  Users ──────────► │  ┌──────────────────────────────┐   │
                    │  │  Gunicorn (4 Uvicorn workers) │   │
                    │  │  ┌────────┐ ┌────────┐       │   │
                    │  │  │Worker 1│ │Worker 2│       │   │
                    │  │  └────────┘ └────────┘       │   │
                    │  │  ┌────────┐ ┌────────┐       │   │
                    │  │  │Worker 3│ │Worker 4│       │   │
                    │  │  └────────┘ └────────┘       │   │
                    │  └──────────┬───────────────────┘   │
                    │             │                        │
                    │  ┌──────────▼──────────┐            │
                    │  │   Redis (Docker)     │            │
                    │  │   Cache layer        │            │
                    │  └─────────────────────┘            │
                    └─────────────┬────────────────────────┘
                                  │ SSL
                    ┌─────────────▼────────────────────────┐
                    │   Neon (Managed Serverless Postgres)  │
                    │   - Auto-scales                       │
                    │   - Sleeps when idle                  │
                    │   - 0.5 GB free                       │
                    └──────────────────────────────────────┘
```

**Why this stack?**
- **Coolify**: Self-hosted PaaS on your VPS. Free, full control, auto-SSL, auto-deploy from Git.
- **Neon**: Serverless Postgres. Scales independently, no Docker volume to manage, free tier generous.
- **Gunicorn + Uvicorn workers**: 4x concurrency over single-process uvicorn.
- **Redis**: Sub-millisecond caching for hot queries. Runs in Docker alongside the app.

---

## 1. Dockerfile

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

EXPOSE 8080

# Gunicorn with 4 async uvicorn workers
CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "4", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
```

**Worker count rule of thumb**: `2 * CPU_CORES + 1`. For a 2-core VPS, 4 workers is optimal.

---

## 2. docker-compose.yml

```yaml
version: "3.9"

services:
  web:
    build: .
    container_name: myapp-web
    env_file: .env
    working_dir: /app
    command: >
      gunicorn app.main:app
      -k uvicorn.workers.UvicornWorker
      --bind 0.0.0.0:8080
      --workers 4
      --timeout 120
      --access-logfile -
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: myapp-redis
    volumes:
      - redis_data:/data
    restart: unless-stopped

volumes:
  redis_data:
```

**Note**: No `db` service — Postgres is on Neon (external).

---

## 3. requirements.txt

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
gunicorn==22.0.0
SQLAlchemy==2.0.35
asyncpg==0.29.0
psycopg[binary]==3.2.1
redis==5.0.7
pydantic-settings==2.4.0
# ... your other deps
```

---

## 4. Neon Setup (5 minutes)

### Create the database

1. Go to [neon.tech](https://neon.tech) and sign up (free)
2. Create a project (pick region closest to your Coolify VPS)
3. Keep **Free plan** — 0.5 GB storage, 100 compute hours/month
4. Copy the connection string:
   ```
   postgresql://neondb_owner:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require
   ```

### Format for your app

In your Coolify env vars, set:
```
DATABASE_URL=postgresql+psycopg://neondb_owner:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require
```

**Important**: Use the `-pooler` endpoint (connection pooling via PgBouncer). This handles many concurrent connections efficiently.

---

## 5. Database Configuration

### config.py — Handle Neon + local fallback

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str | None = None
    REDIS_URL: str | None = None

    # Fallback for local dev (Docker Postgres)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "myapp"
    POSTGRES_USER: str = "myapp"
    POSTGRES_PASSWORD: str = "myapp_pass"

    @property
    def db_url_sync(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def db_url_async(self) -> str:
        url = self.db_url_sync
        if url.startswith("postgresql+psycopg://"):
            url = url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # Strip params not supported by asyncpg
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        params.pop("channel_binding", None)
        params.pop("sslmode", None)  # Passed via connect_args instead
        cleaned_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=cleaned_query))

settings = Settings()
```

### database.py — Connection pool tuned for Neon

```python
import ssl as _ssl
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

class Base(DeclarativeBase):
    pass

# SSL for Neon / cloud Postgres
_connect_args: dict = {}
if "sslmode=require" in (settings.DATABASE_URL or ""):
    _connect_args["ssl"] = _ssl.create_default_context()

engine = create_async_engine(
    settings.db_url_async,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,        # 10 persistent connections per worker
    max_overflow=20,     # Burst to 30 per worker
    pool_recycle=300,    # Recycle every 5 min (Neon closes idle connections)
    pool_timeout=30,     # Wait up to 30s for a connection
    connect_args=_connect_args,
)

async_session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
```

**Key settings for Neon:**
- `pool_recycle=300` — Neon's pooler drops idle connections after ~5 min. This prevents "connection closed" errors.
- `pool_pre_ping=True` — Tests connection before using it. Catches stale connections from Neon sleep.
- `connect_args={"ssl": ...}` — Required for Neon. The `sslmode` URL param doesn't work with asyncpg.

---

## 6. Redis Cache Layer

### cache.py — Graceful fallback if Redis is down

```python
import json
import logging
from typing import Any
from app.config import settings

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

class RedisCache:
    """Async Redis cache with JSON serialization. Falls back to no-op if Redis unavailable."""

    def __init__(self):
        self._pool = None

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
            await self._pool.ping()
            logger.info("Redis connected: %s", settings.REDIS_URL)
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
            raw = await pool.get(f"app:{key}")
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: int = 60) -> None:
        pool = await self._get_pool()
        if pool is None:
            return
        try:
            await pool.set(f"app:{key}", json.dumps(value, default=str), ex=ttl)
        except Exception:
            pass

    async def delete(self, *keys: str) -> None:
        pool = await self._get_pool()
        if pool is None:
            return
        try:
            await pool.delete(*(f"app:{k}" for k in keys))
        except Exception:
            pass

cache = RedisCache()
```

### Usage pattern

```python
from app.cache import cache

# In your route handler
async def get_homepage():
    # Try cache first
    stats = await cache.get("home:stats")
    if stats:
        return stats

    # Cache miss — hit database
    stats = await fetch_stats_from_db()
    await cache.set("home:stats", stats, ttl=30)  # Cache for 30 seconds
    return stats

# When data changes, invalidate
async def create_post():
    # ... save to DB ...
    await cache.delete("home:stats")  # Bust the cache
```

**What to cache (and TTL):**

| Data | TTL | Why |
|------|-----|-----|
| Homepage stats (counts) | 30s | Changes frequently but exact count not critical |
| Channel list | 60s | Rarely changes |
| Leaderboard | 60s | Updates on each post but not time-sensitive |
| Individual post | Don't cache | Changes with each comment/vote |
| User session | Don't cache | Auth should always hit DB |

---

## 7. Coolify Deployment

### Environment variables

Set these in your Coolify service settings:

```env
# App
SECRET_KEY=your-random-secret-key
BASE_URL=https://yourapp.yourdomain.com

# Database (Neon)
DATABASE_URL=postgresql+psycopg://neondb_owner:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require

# Redis (auto-set in docker-compose, but add here as backup)
REDIS_URL=redis://redis:6379/0
```

### Deploy steps

1. Push code to GitHub
2. In Coolify, create a new service from your repo
3. Set the env vars above
4. Deploy — Coolify auto-builds from Dockerfile, sets up SSL, reverse proxy

### After deploy, verify:

```bash
# Health check
curl https://yourapp.yourdomain.com/healthz

# Check Neon connection
curl https://yourapp.yourdomain.com/  # Should load data from Neon
```

---

## 8. Monitoring & Scaling

### When to scale workers

| Metric | Action |
|--------|--------|
| Response time > 500ms consistently | Add workers (4 → 8) |
| CPU > 80% on VPS | Upgrade VPS or split services |
| Neon compute hours hitting limit | Upgrade to Launch plan ($0.10/CU-hr) |
| Redis memory > 100MB | Add eviction policy or upgrade |

### Scaling workers

Change `--workers 4` to `--workers 8` in Dockerfile/docker-compose. Rule: `2 * CPU_CORES + 1`.

### Scaling beyond a single VPS

When you outgrow one VPS:
1. **Database**: Already on Neon — no migration needed
2. **Redis**: Move to Upstash (serverless Redis, free tier: 10K commands/day)
3. **App**: Deploy to Railway/Fly.io with auto-scaling, or add more Coolify nodes

---

## 9. Local Development

For local dev, use Docker Postgres instead of Neon:

```yaml
# docker-compose.dev.yml
version: "3.9"
services:
  web:
    build: .
    env_file: .env.dev
    command: uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
    volumes:
      - .:/app
    depends_on:
      - db
      - redis

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: myapp
      POSTGRES_USER: myapp
      POSTGRES_PASSWORD: myapp_pass
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

```env
# .env.dev
DATABASE_URL=postgresql+psycopg://myapp:myapp_pass@db:5432/myapp
REDIS_URL=redis://redis:6379/0
SECRET_KEY=dev-secret
```

**Note**: Local uses single-process `uvicorn --reload` for hot reloading. Production uses `gunicorn` with workers.

---

## Quick Reference

| Component | Production | Local Dev |
|-----------|-----------|-----------|
| Database | Neon (managed) | Docker Postgres |
| Cache | Redis (Docker) | Redis (Docker) |
| Server | Gunicorn + 4 workers | Uvicorn --reload |
| SSL | Coolify auto-SSL | None (http) |
| Deploy | Git push → auto-deploy | docker-compose up |

---

## Cost

| Service | Free Tier | Paid |
|---------|-----------|------|
| Coolify | Free (self-hosted) | VPS cost only ($5-20/mo) |
| Neon | 0.5 GB, 100 compute-hrs | $0.10/CU-hr + $0.35/GB |
| Redis | Docker (free) | Upstash free: 10K cmd/day |
| **Total** | **$5-20/mo (VPS only)** | **Scales with usage** |
