"""User profile builder — aggregates behavioral stats + LLM domain extraction.

Computes seven profile dimensions from existing data:
  3. Intent preference distribution (from RagTraceRun)
  4. Knowledge-base preference (from Message sources)
  5. Query-style metrics (from Message rag_modes)
  6. Feedback signals (from MessageFeedback)
  2. Professional domain (LLM-extracted from recent conversations)
  7. mem0 facts count (from Mem0Manager)
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.models import (
    User,
    Conversation,
    Message,
    MessageFeedback,
    RagTraceRun,
    UserProfile,
    gen_id,
)
from app.memory.mem0_client import Mem0Manager

_log = get_logger("flavorag.memory.profile_builder")


PROFILE_BUILD_SYSTEM_PROMPT = """你是一个用户画像分析助手。根据用户的近期提问历史和行为统计，生成结构化的用户专业领域画像。

请分析用户的：
1. 专业领域（如：DevOps、数据库、安全、前端开发、后端架构、数据分析等）
2. 专业水平（junior / mid / expert，根据问题深度判断）
3. 领域摘要（1-2句话描述用户的专业背景和常见需求）

返回 JSON 格式：
{
  "domains": ["领域1", "领域2"],
  "expertise_level": "junior|mid|expert",
  "domain_summary": "自然语言摘要"
}
"""


async def _compute_intent_distribution(
    db: AsyncSession, user_id: str
) -> dict[str, float]:
    """Dimension 3: Intent preference distribution from trace runs."""
    rows = await db.execute(
        select(RagTraceRun.intent, func.count(RagTraceRun.id))
        .where(RagTraceRun.user_id == user_id, RagTraceRun.intent.isnot(None))
        .group_by(RagTraceRun.intent)
    )
    counts = {row[0]: row[1] for row in rows if row[0]}
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: round(v / total, 3) for k, v in counts.items()}


async def _compute_kb_preference(
    db: AsyncSession, user_id: str
) -> tuple[list[dict], dict[str, float]]:
    """Dimension 4: Preferred KBs and doc types from message sources."""
    # Get KB preference from trace runs
    from app.models import KnowledgeBase

    kb_rows = await db.execute(
        select(RagTraceRun.kb_id, func.count(RagTraceRun.id))
        .where(RagTraceRun.user_id == user_id, RagTraceRun.kb_id.isnot(None))
        .group_by(RagTraceRun.kb_id)
        .order_by(desc(func.count(RagTraceRun.id)))
        .limit(5)
    )
    kb_counts = [(row[0], row[1]) for row in kb_rows if row[0]]

    preferred_kbs = []
    for kb_id, count in kb_counts:
        kb = await db.execute(
            select(KnowledgeBase.name).where(KnowledgeBase.id == kb_id)
        )
        kb_name = kb.scalar() or kb_id
        preferred_kbs.append({"kb_id": kb_id, "kb_name": kb_name, "count": count})

    # Doc type preference from message sources
    msg_rows = await db.execute(
        select(Message.sources).where(
            Message.user_id == user_id,
            Message.role == "assistant",
            Message.deleted == 0,
        )
    )
    doc_type_counter: Counter = Counter()
    for (sources_json,) in msg_rows:
        if not sources_json:
            continue
        try:
            sources = sources_json if isinstance(sources_json, list) else json.loads(sources_json)
            for src in sources:
                bt = src.get("blockType") or "PARA"
                doc_type_counter[bt] += 1
        except (json.JSONDecodeError, TypeError):
            continue

    total_types = sum(doc_type_counter.values()) or 1
    preferred_doc_types = {k: round(v / total_types, 3) for k, v in doc_type_counter.items()}

    return preferred_kbs, preferred_doc_types


async def _compute_query_style(
    db: AsyncSession, user_id: str
) -> dict[str, float | int]:
    """Dimension 5: Query-style metrics from messages."""
    # Average query length
    user_msgs = await db.execute(
        select(Message.content, Message.rag_modes).where(
            Message.user_id == user_id,
            Message.role == "user",
            Message.deleted == 0,
        )
    )
    rows = user_msgs.all()
    if not rows:
        return {"total_queries": 0, "avg_query_length": 0.0}

    total_queries = len(rows)
    total_length = sum(len(r[0]) for r in rows if r[0])
    avg_length = round(total_length / total_queries, 1) if total_queries else 0.0

    # RAG mode rates
    deep_thinking = 0
    graph_rag = 0
    hyde = 0
    for _, rag_modes in rows:
        if not rag_modes:
            continue
        modes = rag_modes if isinstance(rag_modes, dict) else {}
        if modes.get("deepThinking") or modes.get("deep_thinking"):
            deep_thinking += 1
        if modes.get("graphRag") or modes.get("graph_rag"):
            graph_rag += 1
        if modes.get("hyde"):
            hyde += 1

    return {
        "total_queries": total_queries,
        "avg_query_length": avg_length,
        "deep_thinking_rate": round(deep_thinking / total_queries, 3),
        "graph_rag_rate": round(graph_rag / total_queries, 3),
        "hyde_rate": round(hyde / total_queries, 3),
    }


async def _compute_feedback_signals(
    db: AsyncSession, user_id: str
) -> dict[str, Any]:
    """Dimension 6: Feedback signals from message feedback."""
    up_count = await db.execute(
        select(func.count(MessageFeedback.id)).where(
            MessageFeedback.user_id == user_id,
            MessageFeedback.vote == 1,
            MessageFeedback.deleted == 0,
        )
    )
    down_count = await db.execute(
        select(func.count(MessageFeedback.id)).where(
            MessageFeedback.user_id == user_id,
            MessageFeedback.vote == -1,
            MessageFeedback.deleted == 0,
        )
    )
    thumbs_up = up_count.scalar() or 0
    thumbs_down = down_count.scalar() or 0

    # Follow-up rate: conversations with >1 user message / total conversations
    conv_count = await db.execute(
        select(func.count(func.distinct(Conversation.conversation_id))).where(
            Conversation.user_id == user_id, Conversation.deleted == 0
        )
    )
    total_convs = conv_count.scalar() or 0

    multi_msg_convs = await db.execute(
        select(Conversation.conversation_id, func.count(Message.id))
        .join(Message, Message.conversation_id == Conversation.conversation_id)
        .where(
            Conversation.user_id == user_id,
            Conversation.deleted == 0,
            Message.role == "user",
            Message.deleted == 0,
        )
        .group_by(Conversation.conversation_id)
        .having(func.count(Message.id) > 1)
    )
    multi_count = len(multi_msg_convs.all())
    follow_up_rate = round(multi_count / total_convs, 3) if total_convs else 0.0

    return {
        "thumbs_up_count": thumbs_up,
        "thumbs_down_count": thumbs_down,
        "follow_up_rate": follow_up_rate,
        "total_conversations": total_convs,
    }


async def _llm_extract_domain(
    db: AsyncSession, user_id: str, recent_queries: list[str]
) -> dict[str, Any]:
    """Dimension 2: LLM-extracted professional domain profile."""
    if len(recent_queries) < settings.profile_min_queries_for_build:
        return {"domains": [], "expertise_level": None, "domain_summary": None}

    from app.llm.client import MockLLMClient, get_llm_client

    model = settings.profile_llm_model or "deepseek-v4-flash"
    base_url = (settings.profile_llm_base_url or "https://api.deepseek.com/v1").rstrip("/")
    api_key = (
        settings.profile_llm_api_key
        or settings.mem0_api_key
        or settings.hyde_api_key
    )

    client = get_llm_client(api_key=api_key, base_url=base_url, model=model)
    if isinstance(client, MockLLMClient):
        return {"domains": [], "expertise_level": None, "domain_summary": None}

    queries_text = "\n".join(f"- {q}" for q in recent_queries[:50])
    messages = [
        {"role": "system", "content": PROFILE_BUILD_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户近期提问：\n{queries_text}"},
    ]

    try:
        tokens: list[str] = []
        async with asyncio.timeout(30):
            async for token in client.chat_stream(messages, temperature=0.3):
                if not token.startswith("__THINK__"):
                    tokens.append(token)
                if len("".join(tokens)) > 2048:
                    break

        raw = "".join(tokens).strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start : end + 1])
            return {
                "domains": data.get("domains", []),
                "expertise_level": data.get("expertise_level"),
                "domain_summary": data.get("domain_summary"),
            }
    except Exception as exc:
        _log.warning("profile_llm_extract_failed", error=str(exc)[:200])

    return {"domains": [], "expertise_level": None, "domain_summary": None}


async def build_or_update_profile(
    db: AsyncSession,
    user_id: str,
    tenant_id: str = "default",
) -> UserProfile | None:
    """Build or update a user profile by aggregating all dimensions."""

    # Get recent user queries for LLM extraction
    recent_msgs = await db.execute(
        select(Message.content)
        .where(Message.user_id == user_id, Message.role == "user", Message.deleted == 0)
        .order_by(desc(Message.create_time))
        .limit(50)
    )
    recent_queries = [row[0] for row in recent_msgs if row[0]]

    if not recent_queries:
        _log.debug("profile_build_skipped_no_data", user_id=user_id)
        return None

    # Run all dimension computations in parallel
    intent_dist, kb_prefs, query_style, feedback, domain_profile, mem0_count = (
        await asyncio.gather(
            _compute_intent_distribution(db, user_id),
            _compute_kb_preference(db, user_id),
            _compute_query_style(db, user_id),
            _compute_feedback_signals(db, user_id),
            _llm_extract_domain(db, user_id, recent_queries),
            Mem0Manager.get_instance().count(user_id),
        )
    )

    # Upsert profile
    existing = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = existing.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(
            id=gen_id(),
            user_id=user_id,
            tenant_id=tenant_id,
        )
        db.add(profile)

    # Dimension 2
    profile.domains = domain_profile.get("domains", [])
    profile.expertise_level = domain_profile.get("expertise_level")
    profile.domain_summary = domain_profile.get("domain_summary")

    # Dimension 3
    profile.intent_distribution = intent_dist

    # Dimension 4
    profile.preferred_kbs = kb_prefs[0]
    profile.preferred_doc_types = kb_prefs[1]

    # Dimension 5
    profile.avg_query_length = query_style.get("avg_query_length")
    profile.deep_thinking_rate = query_style.get("deep_thinking_rate")
    profile.graph_rag_rate = query_style.get("graph_rag_rate")
    profile.hyde_rate = query_style.get("hyde_rate")

    # Dimension 6
    profile.thumbs_up_count = feedback.get("thumbs_up_count", 0)
    profile.thumbs_down_count = feedback.get("thumbs_down_count", 0)
    profile.follow_up_rate = feedback.get("follow_up_rate", 0.0)
    profile.total_conversations = feedback.get("total_conversations", 0)

    # Dimension 7
    profile.mem0_facts_count = mem0_count
    profile.mem0_last_sync = datetime.now(timezone.utc).replace(tzinfo=None)

    # Metadata
    profile.total_queries = query_style.get("total_queries", 0)
    profile.last_active_time = datetime.now(timezone.utc).replace(tzinfo=None)
    profile.profile_version = (profile.profile_version or 0) + 1

    await db.flush()
    _log.info(
        "profile_built",
        user_id=user_id,
        version=profile.profile_version,
        queries=profile.total_queries,
        mem0_facts=mem0_count,
    )
    return profile


async def get_profile_for_rag(
    db: AsyncSession,
    user_id: str,
) -> dict[str, Any] | None:
    """Get a lightweight profile dict for RAG pipeline injection.

    Returns None if no profile exists yet (cold-start: TODO default profile).
    """
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        return None

    return {
        "domains": profile.domains or [],
        "expertise_level": profile.expertise_level,
        "domain_summary": profile.domain_summary,
        "intent_distribution": profile.intent_distribution or {},
        "preferred_kbs": profile.preferred_kbs or [],
        "preferred_doc_types": profile.preferred_doc_types or {},
        "avg_query_length": profile.avg_query_length or 0,
        "deep_thinking_rate": profile.deep_thinking_rate or 0,
        "graph_rag_rate": profile.graph_rag_rate or 0,
        "hyde_rate": profile.hyde_rate or 0,
        "thumbs_up_count": profile.thumbs_up_count or 0,
        "thumbs_down_count": profile.thumbs_down_count or 0,
        "total_queries": profile.total_queries or 0,
    }
