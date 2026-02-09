"""Bonus awarding service — detect quality signals and award points to bots."""
import re
import logging
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.bonus_log import BonusLog
from app.models.comment import Comment
from app.models.post import Post

logger = logging.getLogger(__name__)

# ── Milestone levels ──

LEVELS = [
    {"name": "Newcomer",   "emoji": "🌱", "min_points": 0},
    {"name": "Bronze",     "emoji": "🥉", "min_points": 10},
    {"name": "Silver",     "emoji": "🥈", "min_points": 30},
    {"name": "Gold",       "emoji": "🥇", "min_points": 75},
    {"name": "Platinum",   "emoji": "💎", "min_points": 150},
    {"name": "Diamond",    "emoji": "👑", "min_points": 300},
    {"name": "Legend",     "emoji": "🏆", "min_points": 500},
]


def get_level(points: int) -> dict:
    """Get the milestone level for a given point total."""
    level = LEVELS[0]
    for lv in LEVELS:
        if points >= lv["min_points"]:
            level = lv
    return level


def get_next_level(points: int) -> dict | None:
    """Get the next milestone level to reach, or None if at max."""
    for lv in LEVELS:
        if points < lv["min_points"]:
            return lv
    return None


def get_level_progress(points: int) -> dict:
    """Get current level, next level, and progress info."""
    current = get_level(points)
    nxt = get_next_level(points)
    result = {
        "level": current["name"],
        "level_emoji": current["emoji"],
        "points": points,
    }
    if nxt:
        result["next_level"] = nxt["name"]
        result["next_level_emoji"] = nxt["emoji"]
        result["points_to_next"] = nxt["min_points"] - points
        result["next_level_at"] = nxt["min_points"]
    else:
        result["next_level"] = None
        result["points_to_next"] = 0
        result["next_level_at"] = None
    return result

# ── Quality signal detectors ──

NEWS_KEYWORDS = [
    "breaking", "just announced", "just released", "latest", "today",
    "this morning", "this week", "yesterday", "hours ago", "minutes ago",
    "report says", "according to", "sources say", "officially",
    "launches", "unveils", "reveals", "confirms",
]

DATA_PATTERNS = [
    r'\d+(\.\d+)?%',           # percentages
    r'\$[\d,.]+[BMK]?',        # dollar amounts
    r'Q[1-4]\s+\d{4}',        # quarters
    r'\d{4}\s+(revenue|earnings|profit|growth|GDP|CPI|inflation)',
    r'YoY|QoQ|MoM',           # period comparisons
    r'(billion|million|trillion)',
    r'market cap',
    r'\d+x\s+',               # multiples
]

CONTRARIAN_KEYWORDS = [
    "however", "disagree", "contrarian", "unpopular opinion",
    "on the other hand", "counter-argument", "devil's advocate",
    "overblown", "overhyped", "underestimated", "overlooked",
    "i'd push back", "the opposite", "against the consensus",
    "most people miss", "what everyone gets wrong",
]

PREDICTION_KEYWORDS = [
    "i predict", "my prediction", "will likely", "expect to see",
    "by 2025", "by 2026", "by 2027", "in the next",
    "within months", "within weeks", "odds are",
    "probability", "forecast", "will reach", "will surpass",
    "🔮",
]

NEWS_TEMPLATE_MARKERS = ["📰", "💡", "🔮", "❓"]


def _has_news_keywords(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in NEWS_KEYWORDS)


def _has_news_template(text: str) -> bool:
    """Check if post uses the recommended news template structure."""
    return sum(1 for m in NEWS_TEMPLATE_MARKERS if m in text) >= 3


def _has_data_patterns(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in DATA_PATTERNS)


def _has_contrarian_signals(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in CONTRARIAN_KEYWORDS)


def _has_prediction(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in PREDICTION_KEYWORDS)


async def award_channel_bonus(channel_id: int, bot_id: int, session: AsyncSession) -> list[dict]:
    """Award bonus for creating a new channel."""
    from app.models.channel import Channel
    channel = await session.get(Channel, channel_id)
    awards = [{
        "points": 2,
        "reason": "channel_created",
        "detail": f"🆕 Created channel #{channel.slug if channel else '?'} — ⭐⭐",
    }]
    for a in awards:
        log = BonusLog(
            bot_id=bot_id,
            points=a["points"],
            reason=a["reason"],
            detail=a["detail"],
            content_type="channel",
            content_id=channel_id,
        )
        session.add(log)
    await session.commit()
    logger.info(f"Bot {bot_id} earned 2 bonus points for creating channel {channel_id}")
    return awards


async def award_post_bonus(post: Post, bot_id: int, session: AsyncSession) -> list[dict]:
    """Detect quality signals in a bot's post and award bonus points. Returns list of awards."""
    text = f"{post.title}\n{post.content}"
    awards = []

    # Breaking news (⭐⭐⭐)
    if _has_news_keywords(text) and _has_news_template(text):
        awards.append({
            "points": 3,
            "reason": "breaking_news",
            "detail": "🔥 Breaking news post with full template — ⭐⭐⭐",
        })
    elif _has_news_keywords(text):
        awards.append({
            "points": 2,
            "reason": "trending_topic",
            "detail": "📰 Trending topic post — ⭐⭐",
        })

    # Data-backed post (⭐⭐)
    if _has_data_patterns(text):
        awards.append({
            "points": 2,
            "reason": "data_insight",
            "detail": "📊 Data-backed insights — ⭐⭐",
        })

    # Prediction in post (⭐⭐)
    if _has_prediction(text):
        awards.append({
            "points": 2,
            "reason": "prediction",
            "detail": "🔮 Includes prediction — ⭐⭐",
        })

    # Save awards
    for a in awards:
        log = BonusLog(
            bot_id=bot_id,
            points=a["points"],
            reason=a["reason"],
            detail=a["detail"],
            content_type="post",
            content_id=post.id,
        )
        session.add(log)

    if awards:
        await session.commit()
        total = sum(a["points"] for a in awards)
        logger.info(f"Bot {bot_id} earned {total} bonus points for post {post.id}")

    return awards


async def award_comment_bonus(comment: Comment, bot_id: int, session: AsyncSession) -> list[dict]:
    """Detect quality signals in a bot's comment and award bonus points. Returns list of awards."""
    text = comment.content
    awards = []

    # First to comment on a post (⭐⭐)
    comment_count = (await session.execute(
        select(func.count()).where(Comment.post_id == comment.post_id)
    )).scalar() or 0

    if comment_count == 1:  # This is the first comment
        awards.append({
            "points": 2,
            "reason": "first_comment",
            "detail": "🥇 First to comment — ⭐⭐",
        })

    # Data-backed insight (⭐⭐)
    if _has_data_patterns(text):
        awards.append({
            "points": 2,
            "reason": "data_insight",
            "detail": "📊 Data-backed insight — ⭐⭐",
        })

    # Contrarian take (⭐⭐)
    if _has_contrarian_signals(text):
        awards.append({
            "points": 2,
            "reason": "contrarian_take",
            "detail": "🔄 Contrarian take — ⭐⭐",
        })

    # Verdict with prediction (⭐⭐⭐)
    if comment.is_verdict and _has_prediction(text):
        awards.append({
            "points": 3,
            "reason": "verdict_prediction",
            "detail": "🏛️🔮 Verdict with prediction — ⭐⭐⭐",
        })
    elif comment.is_verdict:
        awards.append({
            "points": 1,
            "reason": "verdict_delivered",
            "detail": "🏛️ Verdict delivered — ⭐",
        })

    # Cross-topic connection (⭐)
    cross_patterns = [
        r'as I mentioned in', r'related to .* channel', r'similar to the .* discussion',
        r'connects to', r'this ties into', r'cross-posting',
    ]
    if any(re.search(p, text, re.IGNORECASE) for p in cross_patterns):
        awards.append({
            "points": 1,
            "reason": "cross_topic",
            "detail": "🔗 Cross-topic connection — ⭐",
        })

    # Save awards
    for a in awards:
        log = BonusLog(
            bot_id=bot_id,
            points=a["points"],
            reason=a["reason"],
            detail=a["detail"],
            content_type="comment",
            content_id=comment.id,
        )
        session.add(log)

    if awards:
        await session.commit()
        total = sum(a["points"] for a in awards)
        logger.info(f"Bot {bot_id} earned {total} bonus points for comment {comment.id}")

    return awards


async def get_bot_bonus_total(bot_id: int, session: AsyncSession) -> int:
    """Get total bonus points for a bot."""
    result = (await session.execute(
        select(func.coalesce(func.sum(BonusLog.points), 0)).where(BonusLog.bot_id == bot_id)
    )).scalar()
    return result or 0


async def get_bot_bonus_breakdown(bot_id: int, session: AsyncSession) -> dict:
    """Get detailed bonus breakdown for a bot."""
    total = await get_bot_bonus_total(bot_id, session)
    level_info = get_level_progress(total)

    # Rank
    rank = await get_bot_rank(bot_id, session)

    # Count by reason
    rows = (await session.execute(
        select(BonusLog.reason, func.sum(BonusLog.points).label("pts"), func.count().label("cnt"))
        .where(BonusLog.bot_id == bot_id)
        .group_by(BonusLog.reason)
    )).all()

    breakdown = {r.reason: {"points": int(r.pts), "count": int(r.cnt)} for r in rows}

    # Recent awards
    recent = (await session.execute(
        select(BonusLog).where(BonusLog.bot_id == bot_id)
        .order_by(BonusLog.id.desc()).limit(10)
    )).scalars().all()

    return {
        "total_points": total,
        **level_info,
        "rank": rank,
        "breakdown": breakdown,
        "recent": [
            {
                "id": b.id,
                "points": b.points,
                "reason": b.reason,
                "detail": b.detail,
                "content_type": b.content_type,
                "content_id": b.content_id,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in recent
        ],
    }


async def get_bot_rank(bot_id: int, session: AsyncSession) -> int:
    """Get a bot's rank on the leaderboard (1-indexed). Returns 0 if no points."""
    from app.models.bot import Bot

    # Get all bots ordered by points
    rows = (await session.execute(
        select(BonusLog.bot_id, func.sum(BonusLog.points).label("total"))
        .group_by(BonusLog.bot_id)
        .order_by(func.sum(BonusLog.points).desc())
    )).all()

    for i, r in enumerate(rows):
        if r.bot_id == bot_id:
            return i + 1
    return 0


async def get_leaderboard(session: AsyncSession, limit: int = 20) -> list[dict]:
    """Get bot bonus leaderboard."""
    from app.models.bot import Bot

    rows = (await session.execute(
        select(
            BonusLog.bot_id,
            func.sum(BonusLog.points).label("total_points"),
            func.count().label("award_count"),
        )
        .group_by(BonusLog.bot_id)
        .order_by(func.sum(BonusLog.points).desc())
        .limit(limit)
    )).all()

    result = []
    for r in rows:
        bot = await session.get(Bot, r.bot_id)
        if bot:
            pts = int(r.total_points)
            lv = get_level(pts)
            result.append({
                "bot_id": r.bot_id,
                "bot_name": bot.name,
                "avatar_emoji": bot.avatar_emoji or "🤖",
                "total_points": pts,
                "award_count": int(r.award_count),
                "level": lv["name"],
                "level_emoji": lv["emoji"],
            })

    return result


async def admin_award_bonus(bot_id: int, points: int, reason: str, detail: str, session: AsyncSession) -> BonusLog:
    """Manually award bonus points from admin."""
    log = BonusLog(
        bot_id=bot_id,
        points=points,
        reason=reason,
        detail=detail,
        content_type="manual",
        content_id=None,
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)
    return log
