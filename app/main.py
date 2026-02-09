from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.config import settings
from app.database import engine, Base
from app.routers import auth as auth_router
from app.routers import posts as posts_router
from app.routers import bot_api as bot_api_router
from app.routers import admin as admin_router

app = FastAPI(title=settings.APP_NAME)

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.on_event("startup")
async def on_startup():
    # Auto-create tables for MVP
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Routers
app.include_router(auth_router.router)
app.include_router(posts_router.router)
app.include_router(bot_api_router.router)
app.include_router(admin_router.router)

@app.get("/healthz")
async def healthz():
    return {"ok": True}
