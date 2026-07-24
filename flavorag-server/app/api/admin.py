"""Admin API — health check, trace inspection, system status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from app.database.session import get_db
from app.auth.dependencies import get_current_user, get_admin_user
from app.models import User, RagTraceRun, RagTraceNode
from app.rag.trace import TraceLogger
from app.config.settings import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


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

    # Test DB
    try:
        from app.database.session import engine
        async with engine.connect() as conn:
            await conn.execute(func.now())
    except Exception:
        status["database"] = "error"

    # Test Redis
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        await r.ping()
        status["redis"] = "ok"
    except Exception:
        status["redis"] = "error"

    # Test Milvus
    try:
        from pymilvus import connections, utility
        connections.connect(alias="health", uri=settings.milvus_uri, timeout=5)
        status["milvus"] = "ok" if utility.list_collections() is not None else "ok"
    except Exception:
        status["milvus"] = "error"

    # Test ES
    if settings.es_enabled:
        try:
            from elasticsearch import AsyncElasticsearch
            es = AsyncElasticsearch(settings.es_uris, request_timeout=5)
            await es.ping()
            status["es"] = "ok"
        except Exception:
            status["es"] = "error"

    # Test Graph
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


@router.get("/traces")
async def list_traces(
    page: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List recent trace runs."""
    offset = (page - 1) * limit
    result = await db.execute(
        select(RagTraceRun)
        .order_by(desc(RagTraceRun.create_time))
        .offset(offset)
        .limit(limit)
    )
    traces = result.scalars().all()

    count_result = await db.execute(select(func.count(RagTraceRun.id)))
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
    detail = await tracer.get_trace(trace_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"code": "0", "message": "success", "data": detail}
