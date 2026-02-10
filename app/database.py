import ssl as _ssl
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

class Base(DeclarativeBase):
    pass

# Detect if SSL is needed (Neon, Supabase, any cloud Postgres)
_connect_args: dict = {}
if "sslmode=require" in (settings.DATABASE_URL or ""):
    _connect_args["ssl"] = _ssl.create_default_context()

# Use asyncpg driver for async engine per SQLAlchemy best practices
# Pool sized for 4 gunicorn workers sharing connections
engine = create_async_engine(
    settings.db_url_async,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,          # 10 persistent connections per worker
    max_overflow=20,       # burst up to 30 total per worker
    pool_recycle=300,      # recycle connections every 5 min (important for Neon)
    pool_timeout=30,       # wait up to 30s for a connection
    connect_args=_connect_args,
)

async_session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
