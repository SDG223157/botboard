from fastapi import APIRouter, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.models.post import Post, AuthorType
from app.models.comment import Comment
from app.models.channel import Channel
from app.models.user import User
from app.dependencies import get_current_user_or_none, require_login
from jinja2 import Environment, FileSystemLoader, select_autoescape

env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape()
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    channels = (await session.execute(select(Channel))).scalars().all()
    posts = (await session.execute(select(Post).order_by(Post.id.desc()).limit(50))).scalars().all()
    tpl = env.get_template("home.html")
    return tpl.render(channels=channels, posts=posts, user=user)


@router.get("/c/{slug}", response_class=HTMLResponse)
async def channel_page(
    slug: str,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    ch = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "channel not found")
    posts = (
        await session.execute(
            select(Post).where(Post.channel_id == ch.id).order_by(Post.id.desc()).limit(50)
        )
    ).scalars().all()
    tpl = env.get_template("channel.html")
    return tpl.render(channel=ch, posts=posts, user=user)


@router.post("/c/{slug}/post")
async def create_human_post(
    slug: str,
    title: str = Form(...),
    content: str = Form(...),
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    ch = (await session.execute(select(Channel).where(Channel.slug == slug))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "channel not found")
    post = Post(
        channel_id=ch.id,
        author_type=AuthorType.human,
        author_user_id=user.id,
        title=title,
        content=content,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return RedirectResponse(f"/p/{post.id}", status_code=303)


@router.get("/p/{post_id}", response_class=HTMLResponse)
async def post_detail(
    post_id: int,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user_or_none),
):
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")
    comments = (
        await session.execute(
            select(Comment).where(Comment.post_id == post_id).order_by(Comment.id.asc())
        )
    ).scalars().all()
    # Resolve author names
    if post.author_user_id:
        author = await session.get(User, post.author_user_id)
        post._author_name = author.display_name or author.email if author else "?"
    else:
        from app.models.bot import Bot
        bot = await session.get(Bot, post.author_bot_id) if post.author_bot_id else None
        post._author_name = f"🤖 {bot.name}" if bot else "bot"

    tpl = env.get_template("post.html")
    return tpl.render(post=post, comments=comments, user=user)


@router.post("/p/{post_id}/comment")
async def create_human_comment(
    post_id: int,
    content: str = Form(...),
    user: User = Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")
    comment = Comment(
        post_id=post_id,
        author_type="human",
        author_user_id=user.id,
        content=content,
    )
    session.add(comment)
    await session.commit()
    return RedirectResponse(f"/p/{post_id}", status_code=303)
