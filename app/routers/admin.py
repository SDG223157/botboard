from fastapi import APIRouter, Depends, HTTPException, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.models.channel import Channel
from app.models.bot import Bot
from app.models.api_token import ApiToken
from app.models.user import User
import secrets

router = APIRouter(prefix="/admin", tags=["admin"])

async def require_admin(session: AsyncSession) -> User:
    # MVP: trust first user id=1 as admin; production should decode JWT
    user = await session.get(User, 1)
    if not user or not user.is_admin:
        raise HTTPException(403, "admin required")
    return user

@router.post("/channels/create")
async def create_channel(
    slug: str = Form(...), name: str = Form(...), session: AsyncSession = Depends(get_session)
):
    exist = (await session.execute(select(Channel).where(Channel.slug==slug))).scalar_one_or_none()
    if exist:
        raise HTTPException(400, "slug exists")
    session.add(Channel(slug=slug, name=name))
    await session.commit()
    return {"ok": True}

@router.post("/bots/create")
async def create_bot(
    name: str = Form(...), owner_user_id: int = Form(...), session: AsyncSession = Depends(get_session)
):
    bot = Bot(name=name, owner_id=owner_user_id)
    session.add(bot)
    await session.commit()
    await session.refresh(bot)

    token = secrets.token_urlsafe(32)
    session.add(ApiToken(bot_id=bot.id, name="default", token_hash=token))
    await session.commit()
    return {"bot_id": bot.id, "token": token}
