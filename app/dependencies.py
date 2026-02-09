"""Shared auth dependencies – support both httpOnly cookie and Bearer header."""
from fastapi import Request, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_session
from app.models.user import User
from app.services.auth import verify_access_token

COOKIE_NAME = "bb_token"


async def get_current_user_or_none(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User | None:
    """Return the logged-in User, or None for anonymous visitors."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        # Fall back to Authorization header (useful for API/curl)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        return None
    try:
        data = verify_access_token(token)
    except Exception:
        return None
    user = await session.get(User, int(data["sub"]))
    return user


async def require_login(user: User | None = Depends(get_current_user_or_none)) -> User:
    """Raise 401 if not logged in."""
    if not user:
        raise HTTPException(401, "Login required")
    return user


async def require_admin(user: User = Depends(require_login)) -> User:
    """Raise 403 if not admin."""
    if not user.is_admin:
        raise HTTPException(403, "Admin required")
    return user
