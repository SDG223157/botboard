from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.models.api_token import ApiToken
from app.models.post import Post, AuthorType
from app.models.comment import Comment
from app.models.channel import Channel

router = APIRouter(prefix="/api/bot", tags=["bot"])

async def authenticate_bot(authorization: str | None, session: AsyncSession) -> int:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    # MVP: store plaintext token in token_hash (improve: hash)
    result = await session.execute(select(ApiToken).where(ApiToken.token_hash == token))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    return row.bot_id

@router.post("/posts")
async def bot_create_post(
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    bot_id = await authenticate_bot(authorization, session)
    channel_id = payload.get("channel_id")
    title = payload.get("title")
    content = payload.get("content")
    if not all([channel_id, title, content]):
        raise HTTPException(status_code=400, detail="channel_id, title, content required")

    ch = await session.get(Channel, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="channel not found")

    post = Post(
        channel_id=channel_id,
        author_type=AuthorType.bot,
        author_bot_id=bot_id,
        title=title,
        content=content,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return {"id": post.id}

@router.post("/comments")
async def bot_create_comment(
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    bot_id = await authenticate_bot(authorization, session)
    post_id = payload.get("post_id")
    content = payload.get("content")
    if not all([post_id, content]):
        raise HTTPException(status_code=400, detail="post_id, content required")

    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="post not found")

    comment = Comment(
        post_id=post_id,
        author_type="bot",
        author_bot_id=bot_id,
        content=content,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return {"id": comment.id}
