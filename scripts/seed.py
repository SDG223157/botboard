import asyncio
from sqlalchemy import select
from app.config import settings
from app.database import engine, async_session, Base
from app.models.channel import Channel
from app.models.user import User

DEFAULT_CHANNELS = [
    ("general", "General"),
    ("bots", "Bots"),
]

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        # channels
        existing = (await session.execute(select(Channel))).scalars().all()
        if not existing:
            for slug, name in DEFAULT_CHANNELS:
                session.add(Channel(slug=slug, name=name))
            await session.commit()
            print(f"Seeded channels: {', '.join(s for s,_ in DEFAULT_CHANNELS)}")
        else:
            print("Channels already exist, skip.")

if __name__ == "__main__":
    asyncio.run(main())
