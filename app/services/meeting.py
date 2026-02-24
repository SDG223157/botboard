"""Meeting room scoring: parse peer ratings, compute averages, set dynamic limits."""
import re
from collections import defaultdict
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.comment import Comment
from app.models.bot import Bot
from app.models.meeting_score import MeetingScore

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
