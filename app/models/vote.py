from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, ForeignKey, DateTime, func, UniqueConstraint
from app.database import Base


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_vote_post_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    bot_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("bots.id", ondelete="CASCADE"), nullable=True)
    value: Mapped[int] = mapped_column(Integer, default=1)  # +1 upvote, -1 downvote
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
