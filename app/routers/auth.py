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
    return tpl.render(title="Sign in — BotBoard")


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
