import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.database import get_session
from app.schemas.auth import MagicLinkRequest
from app.models.user import User
from app.services.emailer import send_email
from app.services.auth import generate_magic_link, verify_magic_link, generate_access_token, verify_access_token
from app.config import settings
from app.dependencies import COOKIE_NAME, get_current_user_or_none

_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape()
)

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_OPTS = dict(
    key=COOKIE_NAME,
    httponly=True,
    samesite="lax",
    secure=not settings.BASE_URL.startswith("http://localhost"),
    max_age=settings.ACCESS_TOKEN_EXP_MIN * 60,
    path="/",
)


@router.get("/login", response_class=HTMLResponse)
async def login_page(user: User | None = Depends(get_current_user_or_none)):
    if user:
        return RedirectResponse("/", status_code=302)
    tpl = _env.get_template("login.html")
    return tpl.render(
        title="Sign in — BotBoard",
        telegram_bot_username=settings.TELEGRAM_BOT_USERNAME,
    )


@router.post("/magic-link/request")
async def request_magic_link(payload: MagicLinkRequest, request: Request, session: AsyncSession = Depends(get_session)):
    token = generate_magic_link(payload.email)
    origin = f"{request.url.scheme}://{request.url.netloc}"
    url = f"{origin}/auth/magic-link/callback?token={token}"
    email_html = f"""
    <p>Click to sign in:</p>
    <p><a href='{url}'>Sign in to {settings.APP_NAME}</a></p>
    <p>This link expires in {settings.MAGIC_LINK_EXP_MIN} minutes.</p>
    """
    email_sent = False
    try:
        await send_email(payload.email, f"{settings.APP_NAME} sign-in", email_html)
        email_sent = True
    except Exception:
        pass

    if email_sent:
        return {"ok": True, "method": "email"}
    else:
        return {"ok": True, "method": "link", "url": url}


@router.get("/me")
async def me(user: User | None = Depends(get_current_user_or_none)):
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


@router.get("/magic-link/callback")
async def magic_link_callback(token: str, session: AsyncSession = Depends(get_session)):
    try:
        email = verify_magic_link(token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # upsert user
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=email, display_name=email.split("@")[0])
        session.add(user)
        await session.commit()
        await session.refresh(user)

    # auto-promote first admin
    if settings.AUTO_PROMOTE_FIRST_ADMIN:
        count = (await session.execute(select(User))).scalars().all()
        if len(count) == 1 and not user.is_admin:
            user.is_admin = True
            await session.commit()

    # allowlist promote
    if settings.ADMIN_ALLOWLIST:
        allow = {e.strip().lower() for e in settings.ADMIN_ALLOWLIST.split(",") if e.strip()}
        if user.email.lower() in allow and not user.is_admin:
            user.is_admin = True
            await session.commit()

    access = generate_access_token(str(user.id))

    # Set httpOnly cookie and redirect (also set localStorage for backwards compat)
    html = f"""
    <html><body>
    <script>
      localStorage.setItem('access_token', '{access}');
      window.location.href = '/';
    </script>
    </body></html>
    """
    response = HTMLResponse(content=html)
    response.set_cookie(value=access, **_COOKIE_OPTS)
    return response


def _verify_telegram_auth(data: dict) -> bool:
    """Verify Telegram Login Widget callback data using HMAC-SHA256."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return False
    check_hash = data.get("hash", "")
    # Build check string: sorted key=value pairs excluding 'hash'
    filtered = {k: v for k, v in data.items() if k != "hash" and v}
    check_string = "\n".join(f"{k}={filtered[k]}" for k in sorted(filtered))
    # Secret = SHA256(bot_token)
    secret = hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.encode()).digest()
    computed = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, check_hash)


@router.get("/telegram/callback")
async def telegram_callback(request: Request, session: AsyncSession = Depends(get_session)):
    """Handle Telegram Login Widget callback."""
    params = dict(request.query_params)

    # Verify authenticity
    if not _verify_telegram_auth(params):
        raise HTTPException(400, "Invalid Telegram auth data")

    # Check auth_date is not too old (allow 5 minutes)
    auth_date = int(params.get("auth_date", 0))
    if abs(time.time() - auth_date) > 300:
        raise HTTPException(400, "Telegram auth expired")

    telegram_id = int(params["id"])
    first_name = params.get("first_name", "")
    last_name = params.get("last_name", "")
    username = params.get("username", "")
    photo_url = params.get("photo_url", "")
    display = f"{first_name} {last_name}".strip() or username or f"tg_{telegram_id}"

    # Find user by telegram_id
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    if not user:
        # Create new user — use telegram-based placeholder email
        placeholder_email = f"tg_{telegram_id}@telegram.user"
        user = User(
            email=placeholder_email,
            display_name=display,
            telegram_id=telegram_id,
            telegram_username=username,
            telegram_photo_url=photo_url,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        # Update profile from Telegram
        user.display_name = display
        user.telegram_username = username
        user.telegram_photo_url = photo_url
        await session.commit()

    # Auto-promote first admin
    if settings.AUTO_PROMOTE_FIRST_ADMIN:
        count = (await session.execute(select(User))).scalars().all()
        if len(count) == 1 and not user.is_admin:
            user.is_admin = True
            await session.commit()

    access = generate_access_token(str(user.id))

    html = f"""
    <html><body>
    <script>
      localStorage.setItem('access_token', '{access}');
      window.location.href = '/';
    </script>
    </body></html>
    """
    response = HTMLResponse(content=html)
    response.set_cookie(value=access, **_COOKIE_OPTS)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(COOKIE_NAME, path="/")
    # Also clear localStorage via a tiny redirect page
    html = """
    <html><body>
    <script>
      localStorage.removeItem('access_token');
      window.location.href = '/';
    </script>
    </body></html>
    """
    response = HTMLResponse(content=html)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
