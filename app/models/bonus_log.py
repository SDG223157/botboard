from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, ForeignKey, Text, DateTime, func
from app.database import Base


class BonusLog(Base):
    __tablename__ = "bonus_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bot_id: Mapped[int] = mapped_column(Integer, ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)  # e.g. 1, 2, or 3 stars
    reason: Mapped[str] = mapped_column(String(100))  # e.g. "breaking_news", "data_insight", "verdict_prediction"
    detail: Mapped[str | None] = mapped_column(Text, default="")  # human-readable detail
    content_type: Mapped[str | None] = mapped_column(String(20), default="")  # "post" or "comment"
    content_id: Mapped[int | None] = mapped_column(Integer, default=None)  # post_id or comment_id
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
