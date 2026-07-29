"""Admin API — dashboard, health check, trace inspection."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import (
    User, Conversation, Message, KnowledgeBase, KnowledgeDocument,
    KnowledgeChunk, RagTraceRun,
)
from pydantic import BaseModel

from app.rag.trace import TraceLogger
from app.config.settings import settings
from app.config.hyperparam import (
    list_all_hyperparams,
    update_hyperparam,
    refresh_cache,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class HyperParamUpdate(BaseModel):
    key: str
    value: str


# ---- Dashboard ----


@router.get("/dashboard")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Aggregate dashboard statistics."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    tenant_id = user.tenant_id or "default"
    global_access = user.role == "system_admin"

    # Counts
    kb_count = (await db.execute(
        select(func.count(KnowledgeBase.id)).where(
            KnowledgeBase.deleted == 0,
            *( [] if global_access else [KnowledgeBase.tenant_id == tenant_id] ),
        )
    )).scalar() or 0

    doc_count = (await db.execute(
        select(func.count(KnowledgeDocument.id)).where(
            KnowledgeDocument.deleted == 0,
            *( [] if global_access else [KnowledgeDocument.tenant_id == tenant_id] ),
        )
    )).scalar() or 0

    chunk_count = (await db.execute(
        select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.deleted == 0,
            *( [] if global_access else [KnowledgeChunk.tenant_id == tenant_id] ),
        )
    )).scalar() or 0

    conv_count = (await db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.deleted == 0,
            *( [] if global_access else [Conversation.tenant_id == tenant_id] ),
        )
    )).scalar() or 0

    msg_count = (await db.execute(
        select(func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.deleted == 0,
            *( [] if global_access else [Conversation.tenant_id == tenant_id] ),
        )
    )).scalar() or 0

    # Today's metrics
    today_questions = (await db.execute(
        select(func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.deleted == 0,
            Message.role == "user",
            Message.create_time >= today_start,
            *( [] if global_access else [Conversation.tenant_id == tenant_id] ),
        )
    )).scalar() or 0

    today_traces = (await db.execute(
        select(func.count(RagTraceRun.id)).where(
            RagTraceRun.create_time >= today_start,
            *( [] if global_access else [RagTraceRun.tenant_id == tenant_id] ),
        )
    )).scalar() or 0

    # Average durations from recent traces (last 100)
    avg_search = (await db.execute(
        select(func.avg(RagTraceRun.search_duration_ms))
        .where(
            RagTraceRun.status == "success",
            *( [] if global_access else [RagTraceRun.tenant_id == tenant_id] ),
        )
        .limit(100)
    )).scalar() or 0

    avg_llm = (await db.execute(
        select(func.avg(RagTraceRun.llm_duration_ms))
        .where(
            RagTraceRun.status == "success",
            *( [] if global_access else [RagTraceRun.tenant_id == tenant_id] ),
        )
        .limit(100)
    )).scalar() or 0

    avg_total = (await db.execute(
        select(func.avg(RagTraceRun.total_duration_ms))
        .where(
            RagTraceRun.status == "success",
            *( [] if global_access else [RagTraceRun.tenant_id == tenant_id] ),
        )
        .limit(100)
    )).scalar() or 0

    # Feedback stats
    from app.models import MessageFeedback
    positive_fb = (await db.execute(
        select(func.count(MessageFeedback.id))
        .join(Message, Message.id == MessageFeedback.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            MessageFeedback.vote == 1,
            MessageFeedback.deleted == 0,
            *( [] if global_access else [Conversation.tenant_id == tenant_id] ),
        )
    )).scalar() or 0
    negative_fb = (await db.execute(
        select(func.count(MessageFeedback.id))
        .join(Message, Message.id == MessageFeedback.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            MessageFeedback.vote == -1,
            MessageFeedback.deleted == 0,
            *( [] if global_access else [Conversation.tenant_id == tenant_id] ),
        )
    )).scalar() or 0

    return {"code": "0", "message": "success", "data": {
        "knowledgeBases": kb_count,
        "documents": doc_count,
        "chunks": chunk_count,
        "conversations": conv_count,
        "messages": msg_count,
        "todayQuestions": today_questions,
        "todayTraces": today_traces,
        "avgSearchMs": round(avg_search, 0),
        "avgLlmMs": round(avg_llm, 0),
        "avgTotalMs": round(avg_total, 0),
        "positiveFeedback": positive_fb,
        "negativeFeedback": negative_fb,
    }}


# ---- Health ----


@router.get("/health")
async def health():
    """Detailed health check with component status."""
    status = {
        "server": "ok",
        "database": "ok",
        "redis": "disabled",
        "milvus": "disabled",
        "es": "disabled",
        "graph": "disabled",
        "rate_limit": settings.rate_limit_enabled,
    }

    try:
        from app.database.session import engine
        async with engine.connect() as conn:
            await conn.execute(func.now())
    except Exception:
        status["database"] = "error"

    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        await r.ping()
        status["redis"] = "ok"
    except Exception:
        status["redis"] = "error"

    try:
        from pymilvus import connections, utility
        connections.connect(alias="default", uri=settings.milvus_uri, timeout=5)
        status["milvus"] = "ok" if utility.list_collections() is not None else "ok"
    except Exception:
        status["milvus"] = "error"

    if settings.es_enabled:
        try:
            from elasticsearch import AsyncElasticsearch
            es = AsyncElasticsearch(settings.es_uris, request_timeout=5)
            await es.ping()
            status["es"] = "ok"
        except Exception:
            status["es"] = "error"

    if settings.graph_enabled:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as c:
                resp = await c.get(f"{settings.lightrag_base_url}/health")
                status["graph"] = "ok" if resp.status_code == 200 else "error"
        except Exception:
            status["graph"] = "error"

    overall = all(v in ("ok", "disabled") or v is True or v is False
                  for v in status.values() if isinstance(v, str))

    return {"code": "0", "message": "success", "data": {
        "status": "healthy" if overall else "degraded",
        "components": status,
    }}


# ---- Traces ----


@router.get("/traces")
async def list_traces(
    page: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List recent trace runs."""
    offset = (page - 1) * limit
    tenant_predicate = (
        True
        if user.role == "system_admin"
        else RagTraceRun.tenant_id == (user.tenant_id or "default")
    )
    result = await db.execute(
        select(RagTraceRun)
        .where(tenant_predicate)
        .order_by(desc(RagTraceRun.create_time))
        .offset(offset)
        .limit(limit)
    )
    traces = result.scalars().all()

    count_result = await db.execute(
        select(func.count(RagTraceRun.id)).where(tenant_predicate)
    )
    total = count_result.scalar() or 0

    return {"code": "0", "message": "success", "data": {
        "total": total,
        "items": [{
            "id": t.id,
            "query": t.query[:100],
            "rewrite": t.rewrite_query,
            "intent": t.intent,
            "searchMs": t.search_duration_ms,
            "llmMs": t.llm_duration_ms,
            "totalMs": t.total_duration_ms,
            "recallCount": t.recall_count,
            "finalCount": t.final_count,
            "status": t.status,
            "createTime": str(t.create_time),
        } for t in traces],
    }}


@router.get("/traces/{trace_id}")
async def get_trace_detail(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get full trace detail with all nodes."""
    tracer = TraceLogger(db)
    detail = await tracer.get_trace(
        trace_id,
        tenant_id=None if user.role == "system_admin" else (user.tenant_id or "default"),
    )
    if not detail:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"code": "0", "message": "success", "data": detail}


# ---- Hyperparameter Configuration ----


@router.get("/hyperparams")
async def get_hyperparams(
    user: User = Depends(get_current_user),
):
    """List all adjustable hyperparameters with current values and defaults."""
    tenant_id = user.tenant_id or "default"
    params = await list_all_hyperparams(tenant_id=tenant_id)
    return {"code": "0", "message": "success", "data": params}


@router.put("/hyperparams")
async def put_hyperparam(
    body: HyperParamUpdate,
    user: User = Depends(get_current_user),
):
    """Update a single hyperparameter override."""
    if not body.key or not body.value:
        raise HTTPException(status_code=400, detail="key and value are required")
    tenant_id = user.tenant_id or "default"
    is_new = await update_hyperparam(body.key, body.value, tenant_id=tenant_id)
    return {
        "code": "0",
        "message": "success",
        "data": {"key": body.key, "value": body.value, "isNew": is_new},
    }
