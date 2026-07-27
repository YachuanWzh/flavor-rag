"""Query normalization, conversational rewriting, and bounded decomposition."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import or_, select

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.session import async_session_factory
from app.models import QueryTermMapping

_rewrite_log = get_logger("flavorag.rag.rewrite")


@dataclass(frozen=True)
class RewriteResult:
    original_query: str
    normalized_query: str
    rewritten_query: str
    subqueries: list[str] = field(default_factory=list)
    applied_mappings: list[dict] = field(default_factory=list)


def _split_compound_query(question: str, limit: int) -> list[str]:
    """Split only explicit compound questions and keep the result bounded."""
    parts = [
        item.strip(" ,，;；?？。")
        for item in re.split(
            r"(?:[?？；;\n]+|另外|以及|并且|同时|然后|再问|还有)",
            question,
        )
        if item.strip(" ,，;；?？。")
    ]
    if len(parts) <= 1:
        return [question] if question else []
    return parts[: max(1, limit)]


async def load_term_mappings(
    *,
    kb_id: str | None,
    tenant_id: str,
) -> list[dict]:
    """Load global and KB-local active mappings, preferring longer terms."""
    try:
        async with async_session_factory() as session:
            rows = await session.execute(
                select(QueryTermMapping).where(
                    QueryTermMapping.deleted == 0,
                    QueryTermMapping.enabled == 1,
                    QueryTermMapping.tenant_id == tenant_id,
                    or_(
                        QueryTermMapping.kb_id.is_(None),
                        QueryTermMapping.kb_id == "",
                        *(
                            [QueryTermMapping.kb_id == kb_id]
                            if kb_id
                            else []
                        ),
                    ),
                )
            )
            mappings = [
                {
                    "id": row.id,
                    "source": row.source_term.strip(),
                    "target": row.target_term.strip(),
                    "type": (row.mapping_type or "EXACT").upper(),
                }
                for row in rows.scalars().all()
                if row.source_term and row.target_term
            ]
            return sorted(mappings, key=lambda item: len(item["source"]), reverse=True)
    except Exception as exc:
        # A stale database migration must not make chat unavailable.
        _rewrite_log.warning("term_mapping_load_failed", error=type(exc).__name__)
        return []


def normalize_query(question: str, mappings: list[dict]) -> tuple[str, list[dict]]:
    normalized = " ".join((question or "").split()).strip()
    applied: list[dict] = []
    for mapping in mappings:
        source = mapping["source"]
        target = mapping["target"]
        if not source or not target or target in normalized:
            continue
        flags = re.IGNORECASE if source.isascii() else 0
        pattern = re.escape(source)
        replaced, count = re.subn(pattern, target, normalized, flags=flags)
        if count:
            normalized = replaced
            applied.append({**mapping, "count": count})
    return normalized, applied


async def _bump_mapping_hit_counts(applied: list[dict]) -> None:
    """Increment hit_count for applied mappings (fire-and-forget task).

    SQLite only allows one write transaction at a time. The main request
    may still hold an open transaction when this background task fires,
    causing transient OperationalError ("database is locked").  We retry
    a few times with backoff before giving up.
    """
    if not applied:
        return
    from sqlalchemy import update
    from sqlalchemy.sql import func
    from sqlalchemy.exc import OperationalError as SAOperationalError

    mapping_ids = [m["id"] for m in applied if m.get("id")]
    if not mapping_ids:
        return

    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with async_session_factory() as session:
                await session.execute(
                    update(QueryTermMapping)
                    .where(QueryTermMapping.id.in_(mapping_ids))
                    .values(
                        hit_count=func.coalesce(QueryTermMapping.hit_count, 0) + 1,
                    )
                )
                await session.commit()
            return  # success
        except SAOperationalError:
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            _rewrite_log.warning(
                "term_mapping_hit_count_failed",
                error="OperationalError",
                retries=max_retries,
            )
        except Exception as exc:
            _rewrite_log.warning(
                "term_mapping_hit_count_failed",
                error=type(exc).__name__,
            )
            return


async def rewrite_query_result(
    question: str,
    history: list[dict] | None = None,
    *,
    kb_id: str | None = None,
    tenant_id: str = "default",
    max_queries: int | None = None,
) -> RewriteResult:
    limit = max(1, max_queries or settings.query_decomposition_max_queries)
    mappings = await load_term_mappings(kb_id=kb_id, tenant_id=tenant_id)
    normalized, applied = normalize_query(question, mappings)
    if applied:
        asyncio.create_task(_bump_mapping_hit_counts(applied))
    if not normalized:
        return RewriteResult(question, "", "", [], applied)

    rewritten = normalized
    subqueries = _split_compound_query(normalized, limit)
    if settings.rewrite_enabled:
        key = settings.bailian_api_key or settings.siliconflow_api_key
        if key:
            try:
                llm_result = await rewrite_query_with_llm(
                    normalized,
                    history,
                    max_queries=limit,
                )
                if llm_result:
                    rewritten, llm_subqueries = llm_result
                    subqueries = llm_subqueries or [rewritten]
            except Exception as exc:
                _rewrite_log.warning(
                    "llm_rewrite_failed_fallback",
                    error=type(exc).__name__,
                )

    return RewriteResult(
        original_query=question,
        normalized_query=normalized,
        rewritten_query=rewritten or normalized,
        subqueries=(subqueries or [rewritten or normalized])[:limit],
        applied_mappings=applied,
    )


async def rewrite_query(
    question: str,
    history: list[dict] | None = None,
) -> str | None:
    """Backward-compatible single-string rewrite entry point."""
    result = await rewrite_query_result(question, history)
    if not result.rewritten_query:
        return None
    return (
        result.rewritten_query
        if result.rewritten_query != question
        else None
    )


async def rewrite_query_with_llm(
    question: str,
    history: list[dict] | None = None,
    *,
    max_queries: int = 3,
) -> tuple[str, list[str]] | None:
    """Return a structured rewrite and atomic subqueries from the configured LLM."""
    from app.llm.client import MockLLMClient, get_llm_client

    client = get_llm_client()
    if isinstance(client, MockLLMClient):
        return None

    recent = [
        item
        for item in (history or [])[-6:]
        if item.get("content") and item.get("role") in {"user", "assistant", "system"}
    ]
    history_context = "\n".join(
        f"{item['role']}: {str(item['content'])[:300]}" for item in recent
    )
    prompt = [
        {
            "role": "system",
            "content": (
                "你负责把对话问题改写成可检索查询。消解指代、补全省略、"
                "把口语转成书面语，但不得添加用户没有表达的事实。"
                f"复合问题最多拆成 {max_queries} 个原子问题。"
                '只返回 JSON：{"rewrite":"...","sub_queries":["..."]}'
            ),
        },
        {
            "role": "user",
            "content": f"最近对话：\n{history_context}\n\n当前问题：{question}",
        },
    ]
    tokens: list[str] = []
    async for token in client.chat_stream(prompt, temperature=0.1):
        if not token.startswith("__THINK__"):
            tokens.append(token)
    raw = "".join(tokens).strip()
    fenced = re.search(r"\{.*\}", raw, re.DOTALL)
    if not fenced:
        return None
    payload = json.loads(fenced.group(0))
    rewritten = str(payload.get("rewrite") or question).strip()
    subqueries = [
        str(item).strip()
        for item in payload.get("sub_queries", [])
        if str(item).strip()
    ][:max_queries]
    return rewritten, subqueries or [rewritten]
