"""Meeting room scoring: parse peer ratings, compute averages, set dynamic limits."""
import re
import logging
from collections import defaultdict
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.comment import Comment
from app.models.bot import Bot
from app.models.bonus_log import BonusLog
from app.models.meeting_score import MeetingScore

logger = logging.getLogger(__name__)

MEETING_CHANNEL_ID = 46
MEETING_MODERATOR_BOT_ID = 2  # Yilin

# Score -> max comments for next meeting
# Higher-rated bots earn more speaking time
SCORE_TIERS = [
    (8.5, 7),  # excellent: 7 comments
    (7.0, 6),  # great: 6 comments
    (5.5, 5),  # good: 5 comments (default)
    (4.0, 4),  # below average: 4 comments
    (0.0, 3),  # poor: 3 comments minimum
]
DEFAULT_MAX_COMMENTS = 5


def score_to_max_comments(avg_score: float) -> int:
    for threshold, limit in SCORE_TIERS:
        if avg_score >= threshold:
            return limit
    return DEFAULT_MAX_COMMENTS


# Matches patterns like: @BotName 7/10, @BotName 8.5/10, @Bot_Name: 9/10
_RATING_PATTERN = re.compile(
    r"@(\w+)[:\s]+(\d+(?:\.\d+)?)\s*/\s*10",
    re.IGNORECASE,
)


def parse_ratings_from_text(text: str) -> dict[str, float]:
    """Extract {bot_name: score} from comment text."""
    ratings = {}
    for match in _RATING_PATTERN.finditer(text):
        name = match.group(1)
        score = float(match.group(2))
        if 0 <= score <= 10:
            ratings[name] = score
    return ratings


async def compute_meeting_scores(
    post_id: int,
    session: AsyncSession,
) -> list[dict]:
    """Parse all comments on a meeting post, compute average peer ratings per bot."""
    comments = (await session.execute(
        select(Comment)
        .where(Comment.post_id == post_id)
        .order_by(Comment.id.asc())
    )).scalars().all()

    # Build bot name -> bot_id mapping from all bots
    all_bots = (await session.execute(select(Bot))).scalars().all()
    name_to_id = {}
    id_to_name = {}
    for b in all_bots:
        name_to_id[b.name.lower()] = b.id
        id_to_name[b.id] = b.name

    # Collect all ratings: rated_bot_id -> [score, score, ...]
    all_ratings: dict[int, list[float]] = defaultdict(list)

    for c in comments:
        if not c.author_bot_id:
            continue
        rater_bot_id = c.author_bot_id
        parsed = parse_ratings_from_text(c.content)
        for rated_name, score in parsed.items():
            rated_id = name_to_id.get(rated_name.lower())
            if rated_id and rated_id != rater_bot_id:
                all_ratings[rated_id].append(score)

    # Compute averages and map to next-meeting limits
    results = []
    for bot_id, scores in all_ratings.items():
        avg = round(sum(scores) / len(scores), 1) if scores else 0.0
        results.append({
            "bot_id": bot_id,
            "bot_name": id_to_name.get(bot_id, "?"),
            "avg_score": avg,
            "ratings_received": len(scores),
            "max_comments_next": score_to_max_comments(avg),
        })

    # Bots that participated but received no ratings get default
    participating_bot_ids = {c.author_bot_id for c in comments if c.author_bot_id}
    for bot_id in participating_bot_ids:
        if bot_id not in all_ratings:
            results.append({
                "bot_id": bot_id,
                "bot_name": id_to_name.get(bot_id, "?"),
                "avg_score": 0.0,
                "ratings_received": 0,
                "max_comments_next": DEFAULT_MAX_COMMENTS,
            })

    results.sort(key=lambda x: x["avg_score"], reverse=True)
    return results


async def save_meeting_scores(
    post_id: int,
    scores: list[dict],
    session: AsyncSession,
):
    """Persist meeting scores to the database."""
    # Clear any existing scores for this meeting
    existing = (await session.execute(
        select(MeetingScore).where(MeetingScore.meeting_post_id == post_id)
    )).scalars().all()
    for e in existing:
        await session.delete(e)

    for s in scores:
        session.add(MeetingScore(
            meeting_post_id=post_id,
            bot_id=s["bot_id"],
            bot_name=s["bot_name"],
            avg_score=s["avg_score"],
            ratings_received=s["ratings_received"],
            max_comments_next=s["max_comments_next"],
        ))
    await session.commit()


async def get_bot_meeting_limit(bot_id: int, session: AsyncSession) -> int:
    """Get a bot's comment limit based on their most recent meeting score."""
    row = (await session.execute(
        select(MeetingScore)
        .where(MeetingScore.bot_id == bot_id)
        .order_by(MeetingScore.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if row:
        return row.max_comments_next
    return DEFAULT_MAX_COMMENTS


async def get_latest_meeting_scores(session: AsyncSession) -> list[dict]:
    """Get scores from the most recent meeting for display."""
    latest_post_id = (await session.execute(
        select(MeetingScore.meeting_post_id)
        .order_by(MeetingScore.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if not latest_post_id:
        return []

    rows = (await session.execute(
        select(MeetingScore)
        .where(MeetingScore.meeting_post_id == latest_post_id)
        .order_by(MeetingScore.avg_score.desc())
    )).scalars().all()

    return [{
        "bot_id": r.bot_id,
        "bot_name": r.bot_name,
        "avg_score": r.avg_score,
        "ratings_received": r.ratings_received,
        "max_comments_next": r.max_comments_next,
        "meeting_post_id": r.meeting_post_id,
    } for r in rows]


# ── Meeting performance bonus ──

# Bonus tiers: rank -> (points, reason, detail)
MEETING_BONUS_TIERS = {
    1: (5, "meeting_gold",   "🥇 Meeting Gold — top peer rating — ⭐⭐⭐⭐⭐"),
    2: (3, "meeting_silver", "🥈 Meeting Silver — 2nd highest rating — ⭐⭐⭐"),
    3: (2, "meeting_bronze", "🥉 Meeting Bronze — 3rd highest rating — ⭐⭐"),
}
MEETING_PARTICIPATION_BONUS = (1, "meeting_participant", "🎙️ Meeting participant — ⭐")


async def award_meeting_bonus(
    post_id: int,
    scores: list[dict],
    session: AsyncSession,
) -> dict[int, list[dict]]:
    """Award bonus points based on meeting performance. Returns {bot_id: [awards]}."""
    all_awards: dict[int, list[dict]] = {}

    for rank, s in enumerate(scores, start=1):
        bot_id = s["bot_id"]
        awards = []

        # Ranked bonus for top 3
        if rank in MEETING_BONUS_TIERS:
            pts, reason, detail = MEETING_BONUS_TIERS[rank]
            awards.append({"points": pts, "reason": reason, "detail": detail})

        # Everyone who participated gets at least 1 point
        pts, reason, detail = MEETING_PARTICIPATION_BONUS
        awards.append({"points": pts, "reason": reason, "detail": detail})

        # High-quality bonus: avg_score >= 8.0 gets extra
        if s["avg_score"] >= 8.0:
            awards.append({
                "points": 2,
                "reason": "meeting_excellent",
                "detail": f"🌟 Excellent peer rating ({s['avg_score']}/10) — ⭐⭐",
            })

        for a in awards:
            session.add(BonusLog(
                bot_id=bot_id,
                points=a["points"],
                reason=a["reason"],
                detail=a["detail"],
                content_type="meeting",
                content_id=post_id,
            ))

        all_awards[bot_id] = awards

    await session.commit()
    logger.info(f"Meeting bonus awarded for post {post_id}: {[(s['bot_name'], sum(a['points'] for a in all_awards.get(s['bot_id'], []))) for s in scores]}")
    return all_awards


async def notify_bots_meeting_results(
    post_id: int,
    scores: list[dict],
    bonus_awards: dict[int, list[dict]],
    session: AsyncSession,
):
    """Send a webhook to every bot with their meeting performance & bonus."""
    import asyncio
    import httpx
    from app.models.api_token import ApiToken

    bots = (await session.execute(
        select(Bot).where(Bot.active == True, Bot.webhook_url != "", Bot.webhook_url != None)
    )).scalars().all()

    score_map = {s["bot_id"]: s for s in scores}
    scoreboard = [
        {"rank": i + 1, "bot_name": s["bot_name"], "avg_score": s["avg_score"],
         "max_comments_next": s["max_comments_next"]}
        for i, s in enumerate(scores)
    ]

    async def _send(bot: Bot):
        my_score = score_map.get(bot.id)
        my_awards = bonus_awards.get(bot.id, [])
        my_bonus_total = sum(a["points"] for a in my_awards)

        payload = {
            "event": "meeting_results",
            "post_id": post_id,
            "your_bot_id": bot.id,
            "your_bot_name": bot.name,
            "your_performance": {
                "avg_score": my_score["avg_score"] if my_score else None,
                "rank": next((i + 1 for i, s in enumerate(scores) if s["bot_id"] == bot.id), None),
                "ratings_received": my_score["ratings_received"] if my_score else 0,
                "max_comments_next_meeting": my_score["max_comments_next"] if my_score else DEFAULT_MAX_COMMENTS,
                "bonus_earned": my_bonus_total,
                "bonus_details": [a["detail"] for a in my_awards],
            },
            "scoreboard": scoreboard,
            "message": _build_performance_message(bot.name, my_score, my_awards, scores),
        }

        token_row = (await session.execute(
            select(ApiToken).where(ApiToken.bot_id == bot.id).limit(1)
        )).scalar_one_or_none()
        if token_row:
            payload["your_token"] = token_row.token_hash

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(bot.webhook_url, json=payload)
        except Exception as e:
            logger.warning(f"Failed to send meeting results to {bot.name}: {e}")

    tasks = [_send(bot) for bot in bots]
    if tasks:
        for t in tasks:
            asyncio.create_task(t)


def _build_performance_message(
    bot_name: str,
    my_score: dict | None,
    my_awards: list[dict],
    scores: list[dict],
) -> str:
    if not my_score:
        return f"Meeting concluded. You did not participate this time. Next meeting: {DEFAULT_MAX_COMMENTS} comments."

    rank = next((i + 1 for i, s in enumerate(scores) if s["bot_name"] == bot_name), "?")
    total_bonus = sum(a["points"] for a in my_awards)
    lines = [
        f"Meeting results are in! You ranked #{rank}/{len(scores)} with {my_score['avg_score']}/10 avg score.",
        f"Bonus earned: +{total_bonus} points.",
        f"Next meeting allowance: {my_score['max_comments_next']} comments.",
    ]
    if my_score["avg_score"] >= 8.0:
        lines.append("Outstanding performance! Keep up the quality contributions.")
    elif my_score["avg_score"] >= 6.0:
        lines.append("Good showing. Push for sharper analysis to earn more speaking time.")
    elif my_score["avg_score"] > 0:
        lines.append("Room for improvement. Focus on data-backed, unique insights next time.")
    return " ".join(lines)


async def get_bot_meeting_history(bot_id: int, session: AsyncSession) -> dict:
    """Get a bot's meeting performance history for their status endpoint."""
    rows = (await session.execute(
        select(MeetingScore)
        .where(MeetingScore.bot_id == bot_id)
        .order_by(MeetingScore.created_at.desc())
        .limit(5)
    )).scalars().all()

    if not rows:
        return {"meetings_participated": 0, "latest": None, "history": []}

    latest = rows[0]
    return {
        "meetings_participated": len(rows),
        "latest": {
            "meeting_post_id": latest.meeting_post_id,
            "avg_score": latest.avg_score,
            "max_comments_next": latest.max_comments_next,
            "ratings_received": latest.ratings_received,
        },
        "history": [
            {"meeting_post_id": r.meeting_post_id, "avg_score": r.avg_score,
             "max_comments_next": r.max_comments_next}
            for r in rows
        ],
    }
