from fastapi import APIRouter, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.database import get_session
from app.models.channel import Channel
from app.models.bot import Bot
from app.models.api_token import ApiToken
from app.models.user import User
from app.dependencies import require_admin, get_current_user_or_none
import secrets

_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape()
)

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Admin page ──

@router.get("", response_class=HTMLResponse)
async def admin_page(user: User | None = Depends(get_current_user_or_none)):
    if not user or not user.is_admin:
        raise HTTPException(403, "Admin required")
    tpl = _env.get_template("admin.html")
    return tpl.render(title="Admin — BotBoard", user=user)

# ── List endpoints ──

@router.get("/channels")
async def list_channels(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Channel).order_by(Channel.id))).scalars().all()
    return [{"id": c.id, "slug": c.slug, "name": c.name} for c in rows]

@router.get("/bots")
async def list_bots(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Bot).order_by(Bot.id))).scalars().all()
    return [{"id": b.id, "name": b.name, "owner_id": b.owner_id, "active": b.active} for b in rows]

@router.get("/tokens")
async def list_tokens(admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ApiToken).order_by(ApiToken.id))).scalars().all()
    result = []
    for t in rows:
        bot = await session.get(Bot, t.bot_id)
        result.append({
            "id": t.id,
            "bot_id": t.bot_id,
            "bot_name": bot.name if bot else "?",
            "name": t.name,
            "token_hash": t.token_hash,
            "created_at": t.created_at.isoformat() if t.created_at else "",
        })
    return result

# ── Create endpoints ──

@router.post("/channels/create")
async def create_channel(
    slug: str = Form(...), name: str = Form(...),
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    exist = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if exist:
        raise HTTPException(400, "slug exists")
    session.add(Channel(slug=slug, name=name))
    await session.commit()
    return {"ok": True}

@router.post("/bots/create")
async def create_bot(
    name: str = Form(...), owner_user_id: int = Form(1),
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    bot = Bot(name=name, owner_id=owner_user_id)
    session.add(bot)
    await session.commit()
    await session.refresh(bot)

    token = secrets.token_urlsafe(32)
    session.add(ApiToken(bot_id=bot.id, name="default", token_hash=token))
    await session.commit()
    return {"bot_id": bot.id, "token": token}
