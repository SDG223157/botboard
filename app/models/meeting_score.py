from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, func
from app.database import Base


class MeetingScore(Base):
    __tablename__ = "meeting_scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    meeting_post_id: Mapped[int] = mapped_column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    bot_id: Mapped[int] = mapped_column(Integer, ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    bot_name: Mapped[str] = mapped_column(String(100), default="")
    avg_score: Mapped[float] = mapped_column(Float, default=0.0)
    ratings_received: Mapped[int] = mapped_column(Integer, default=0)
    max_comments_next: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
