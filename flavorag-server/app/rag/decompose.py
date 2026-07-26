from __future__ import annotations

import re

from app.config.settings import settings


async def decompose_query(question: str, *, max_queries: int | None = None) -> list[str]:
    """Conservatively split explicit compound questions.

    Retrieval decomposition deliberately uses deterministic syntax here. A
    model planner may propose subqueries in controlled Agentic RAG, but this
    baseline cannot manufacture hidden assumptions.
    """
    cleaned = " ".join(question.split()).strip()
    if not cleaned:
        return []
    if not settings.query_decomposition_enabled:
        return [cleaned]
    limit = max(1, max_queries or settings.query_decomposition_max_queries)
    parts = [
        part.strip(" ,，;；?？")
        for part in re.split(r"(?:以及|并且|同时|然后|；|;|\?{2,}|？{2,})", cleaned)
        if part.strip(" ,，;；?？")
    ]
    if len(parts) <= 1:
        return [cleaned]
    return parts[:limit]
