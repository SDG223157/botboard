import hashlib
import hmac
import time
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.database import get_session
from app.models.user import User
from app.services.auth import generate_access_token
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

# Store OAuth state nonces (in-memory; fine for single instance)
_oauth_states: dict[str, float] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_origin(request: Request) -> str:
    """Get the public-facing origin, respecting reverse proxy headers."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{proto}://{host}"


def _set_login_cookie(user_id: int) -> HTMLResponse:
    """Generate JWT, set cookie + localStorage, redirect to /."""
    access = generate_access_token(str(user_id))
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


async def _upsert_user(session: AsyncSession, **kwargs) -> User:
    """Find or create user, auto-promote first admin."""
    email = kwargs.get("email")
    telegram_id = kwargs.get("telegram_id")

    user = None
    if telegram_id:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
    if not user and email:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    if not user:
        user = User(**{k: v for k, v in kwargs.items() if v is not None})
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        # Update fields
        for k, v in kwargs.items():
            if v is not None and k != "email":
                setattr(user, k, v)
        await session.commit()

    # Auto-promote first admin
    if settings.AUTO_PROMOTE_FIRST_ADMIN:
        count = (await session.execute(select(User))).scalars().all()
        if len(count) == 1 and not user.is_admin:
            user.is_admin = True
            await session.commit()

    # Allowlist promote
    if settings.ADMIN_ALLOWLIST and user.email:
        allow = {e.strip().lower() for e in settings.ADMIN_ALLOWLIST.split(",") if e.strip()}
        if user.email.lower() in allow and not user.is_admin:
            user.is_admin = True
            await session.commit()

    return user


# ── Login page ────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(user: User | None = Depends(get_current_user_or_none)):
    if user:
        return RedirectResponse("/", status_code=302)
    tpl = _env.get_template("login.html")
    return tpl.render(
        title="Sign in — BotBoard",
        telegram_bot_username=settings.TELEGRAM_BOT_USERNAME,
        google_client_id=settings.GOOGLE_CLIENT_ID,
    )


# ── Google OAuth ──────────────────────────────────────────────────────────────

@router.get("/google/login")
async def google_login(request: Request):
    """Redirect to Google OAuth consent screen."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(503, "Google OAuth not configured")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.time()

    # Clean expired states (older than 10 min)
    now = time.time()
    expired = [k for k, v in _oauth_states.items() if now - v > 600]
    for k in expired:
        _oauth_states.pop(k, None)

    origin = _get_origin(request)
    redirect_uri = f"{origin}/auth/google/callback"

    params = urllib.parse.urlencode({
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Handle Google OAuth callback."""
    if error:
        raise HTTPException(400, f"Google auth error: {error}")
    if not code or not state:
        raise HTTPException(400, "Missing code or state")

    # Verify state
    if state not in _oauth_states:
        raise HTTPException(400, "Invalid state — try logging in again")
    _oauth_states.pop(state)

    origin = _get_origin(request)
    redirect_uri = f"{origin}/auth/google/callback"

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(400, "Failed to exchange Google auth code")
        tokens = token_resp.json()

        # Get user info
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(400, "Failed to get Google user info")
        info = userinfo_resp.json()

    email = info.get("email", "")
    if not email:
        raise HTTPException(400, "No email from Google")

    user = await _upsert_user(
        session,
        email=email,
        display_name=info.get("name", email.split("@")[0]),
    )

    return _set_login_cookie(user.id)


# ── Telegram Login ────────────────────────────────────────────────────────────

def _verify_telegram_auth(data: dict) -> bool:
    """Verify Telegram Login Widget callback data using HMAC-SHA256."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return False
    check_hash = data.get("hash", "")
    filtered = {k: v for k, v in data.items() if k != "hash" and v}
    check_string = "\n".join(f"{k}={filtered[k]}" for k in sorted(filtered))
    secret = hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.encode()).digest()
    computed = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, check_hash)


@router.get("/telegram/callback")
async def telegram_callback(request: Request, session: AsyncSession = Depends(get_session)):
    """Handle Telegram Login Widget callback."""
    params = dict(request.query_params)

    if not _verify_telegram_auth(params):
        raise HTTPException(400, "Invalid Telegram auth data")

    auth_date = int(params.get("auth_date", 0))
    if abs(time.time() - auth_date) > 300:
        raise HTTPException(400, "Telegram auth expired")

    telegram_id = int(params["id"])
    first_name = params.get("first_name", "")
    last_name = params.get("last_name", "")
    username = params.get("username", "")
    photo_url = params.get("photo_url", "")
    display = f"{first_name} {last_name}".strip() or username or f"tg_{telegram_id}"

    user = await _upsert_user(
        session,
        email=f"tg_{telegram_id}@telegram.user",
        display_name=display,
        telegram_id=telegram_id,
        telegram_username=username,
        telegram_photo_url=photo_url,
    )

    return _set_login_cookie(user.id)


# ── Me / Logout ───────────────────────────────────────────────────────────────

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


@router.get("/logout")
async def logout():
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
