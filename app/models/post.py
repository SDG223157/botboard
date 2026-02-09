from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, ForeignKey, Text, DateTime, func, Enum
from app.database import Base
import enum

class AuthorType(str, enum.Enum):
    human = "human"
    bot = "bot"

class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("channels.id", ondelete="CASCADE"))

    author_type: Mapped[str] = mapped_column(Enum(AuthorType))
    author_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    author_bot_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("bots.id", ondelete="SET NULL"))

    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    channel = relationship("Channel", back_populates="posts")
