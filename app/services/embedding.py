"""Optional embedding service for pgvector semantic search. Uses OpenRouter or OpenAI when API key is set."""

from __future__ import annotations

from app.config import settings

EMBEDDING_DIM = 1536


def _embedding_client():
    """Return (api_key, base_url). Prefer OpenRouter if OPENROUTER_API_KEY is set."""
    if settings.OPENROUTER_API_KEY:
        return settings.OPENROUTER_API_KEY, "https://openrouter.ai/api/v1"
    if settings.OPENAI_API_KEY:
        return settings.OPENAI_API_KEY, None  # default OpenAI base
    return None, None


async def get_embedding(text: str) -> list[float] | None:
    """Return 1536-dim embedding for text, or None if embedding is not configured."""
    if not text or not (text := text.strip()):
        return None
    api_key, base_url = _embedding_client()
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = AsyncOpenAI(**kwargs)
        # Truncate to avoid token limits (e.g. 8k tokens for embedding models)
        truncated = text[:8000] if len(text) > 8000 else text
        r = await client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=truncated,
        )
        return r.data[0].embedding
    except Exception:
        return None


async def update_post_embedding(post_id: int, text: str) -> None:
    """Compute embedding for text and set it on the post. Run in background; no await in request."""
    emb = await get_embedding(text)
    if not emb:
        return
    try:
        from sqlalchemy import text as sql_text
        from app.database import engine
        async with engine.begin() as conn:
            # pgvector expects a string representation of the list
            vec_str = "[" + ",".join(str(x) for x in emb) + "]"
            await conn.execute(
                sql_text("UPDATE posts SET embedding = :vec::vector WHERE id = :id"),
                {"vec": vec_str, "id": post_id},
            )
    except Exception:
        pass


def _embedding_to_vec_str(emb: list[float]) -> str:
    return "[" + ",".join(str(x) for x in emb) + "]"


async def semantic_search_post_ids(
    query_embedding: list[float],
    limit: int = 20,
    offset: int = 0,
    channel_id: int | None = None,
) -> tuple[list[int], int]:
    """Return (list of post IDs ordered by cosine similarity, total count). Uses raw SQL for <=>."""
    from sqlalchemy import text as sql_text
    from app.database import engine
    vec_str = _embedding_to_vec_str(query_embedding)
    channel_clause = "AND channel_id = :channel_id" if channel_id is not None else ""
    count_sql = f"""
        SELECT COUNT(*) FROM posts
        WHERE embedding IS NOT NULL {channel_clause}
    """
    list_sql = f"""
        SELECT id FROM posts
        WHERE embedding IS NOT NULL {channel_clause}
        ORDER BY embedding <=> :vec::vector
        LIMIT :limit OFFSET :offset
    """
    params = {"vec": vec_str, "limit": limit, "offset": offset}
    count_params = {}
    if channel_id is not None:
        params["channel_id"] = channel_id
        count_params["channel_id"] = channel_id
    async with engine.connect() as conn:
        total = (await conn.execute(sql_text(count_sql), count_params)).scalar() or 0
        r = await conn.execute(sql_text(list_sql), params)
        rows = r.fetchall()
    return [row[0] for row in rows], total
