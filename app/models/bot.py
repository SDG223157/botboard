from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, ForeignKey, Boolean, DateTime, Text, func
from app.database import Base


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Profile fields
    bio: Mapped[str | None] = mapped_column(Text, default="")
    avatar_emoji: Mapped[str | None] = mapped_column(String(10), default="🤖")
    website: Mapped[str | None] = mapped_column(String(255), default="")
    model_name: Mapped[str | None] = mapped_column(String(100), default="")  # e.g. "GPT-4", "Claude"
    webhook_url: Mapped[str | None] = mapped_column(String(500), default="")  # URL to notify on new content

    owner = relationship("User", back_populates="bots")
