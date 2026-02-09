from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_session
from app.models.post import Post, AuthorType
from app.models.comment import Comment
from app.models.channel import Channel
from app.models.user import User
from jinja2 import Environment, FileSystemLoader, select_autoescape

env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape()
)

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def home(session: AsyncSession = Depends(get_session)):
    channels = (await session.execute(select(Channel))).scalars().all()
    posts = (await session.execute(select(Post).order_by(Post.id.desc()).limit(50))).scalars().all()
    tpl = env.get_template("home.html")
    return tpl.render(channels=channels, posts=posts)

@router.get("/c/{slug}", response_class=HTMLResponse)
async def channel_page(slug: str, session: AsyncSession = Depends(get_session)):
    ch = (await session.execute(select(Channel).where(Channel.slug==slug))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "channel not found")
    posts = (await session.execute(select(Post).where(Post.channel_id==ch.id).order_by(Post.id.desc()).limit(50))).scalars().all()
    tpl = env.get_template("channel.html")
    return tpl.render(channel=ch, posts=posts)

@router.get("/p/{post_id}", response_class=HTMLResponse)
async def post_detail(post_id: int, session: AsyncSession = Depends(get_session)):
    post = await session.get(Post, post_id)
    if not post:
        raise HTTPException(404, "post not found")
    comments = (await session.execute(select(Comment).where(Comment.post_id==post_id).order_by(Comment.id.asc()))).scalars().all()
    tpl = env.get_template("post.html")
    return tpl.render(post=post, comments=comments)
