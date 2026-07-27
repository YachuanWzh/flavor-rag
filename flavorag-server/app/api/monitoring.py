"""System monitoring API — business-level aggregation for the admin console.

Complements the Prometheus ``/metrics`` endpoint: this reads relational data
(RAG traces, ingestion outbox jobs, documents) so the admin frontend can show
platform health without a Grafana deployment.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_db
from app.models import (
    IngestionJob,
    KnowledgeDocument,
    RagTraceRun,
    User,
)

router = APIRouter(prefix="/api/admin/monitoring", tags=["monitoring"])

_JOB_STATUSES = ("QUEUED", "RUNNING", "RETRY", "SUCCESS", "DEAD")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * ratio))
    return int(ordered[index])


def _is_global(user: User) -> bool:
    """Admins see platform-wide data; regular users see their tenant only."""
    return user.role in ("admin", "system_admin")


def _tenant_scope(user: User, column):
    return [] if _is_global(user) else [column == (user.tenant_id or "default")]


@router.get("/summary")
async def monitoring_summary(
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Aggregated RAG / ingestion / document health for the given window."""
    since = _utcnow() - timedelta(hours=hours)

    # --- RAG runs ---
    rag_rows = (
        await db.execute(
            select(RagTraceRun.status, func.count(RagTraceRun.id))
            .where(
                RagTraceRun.create_time >= since,
                *_tenant_scope(user, RagTraceRun.tenant_id),
            )
            .group_by(RagTraceRun.status)
        )
    ).all()
    rag_by_status = {status: count for status, count in rag_rows}
    rag_total = sum(rag_by_status.values())
    rag_success = rag_by_status.get("success", 0)

    duration_rows = (
        await db.execute(
            select(
                RagTraceRun.total_duration_ms,
                RagTraceRun.search_duration_ms,
                RagTraceRun.llm_duration_ms,
            )
            .where(
                RagTraceRun.create_time >= since,
                RagTraceRun.status == "success",
                RagTraceRun.total_duration_ms.is_not(None),
                *_tenant_scope(user, RagTraceRun.tenant_id),
            )
            .order_by(desc(RagTraceRun.create_time))
            .limit(1000)
        )
    ).all()
    totals = [row[0] for row in duration_rows if row[0] is not None]
    searches = [row[1] for row in duration_rows if row[1] is not None]
    llms = [row[2] for row in duration_rows if row[2] is not None]

    # --- Ingestion outbox ---
    job_rows = (
        await db.execute(
            select(IngestionJob.status, func.count(IngestionJob.id))
            .where(
                IngestionJob.deleted == 0,
                *_tenant_scope(user, IngestionJob.tenant_id),
            )
            .group_by(IngestionJob.status)
        )
    ).all()
    jobs_by_status = {status: count for status, count in job_rows}

    job_window_rows = (
        await db.execute(
            select(IngestionJob.status, func.count(IngestionJob.id))
            .where(
                IngestionJob.deleted == 0,
                IngestionJob.completed_at >= since,
                *_tenant_scope(user, IngestionJob.tenant_id),
            )
            .group_by(IngestionJob.status)
        )
    ).all()
    jobs_window = {status: count for status, count in job_window_rows}

    avg_job_ms = (
        await db.execute(
            select(func.avg(IngestionJob.duration_ms)).where(
                IngestionJob.deleted == 0,
                IngestionJob.status == "SUCCESS",
                IngestionJob.completed_at >= since,
                *_tenant_scope(user, IngestionJob.tenant_id),
            )
        )
    ).scalar() or 0

    # --- Documents ---
    doc_rows = (
        await db.execute(
            select(KnowledgeDocument.status, func.count(KnowledgeDocument.id))
            .where(
                KnowledgeDocument.deleted == 0,
                *_tenant_scope(user, KnowledgeDocument.tenant_id),
            )
            .group_by(KnowledgeDocument.status)
        )
    ).all()
    docs_by_status = {status: count for status, count in doc_rows}

    return {"code": "0", "message": "success", "data": {
        "windowHours": hours,
        "rag": {
            "total": rag_total,
            "success": rag_success,
            "failed": rag_total - rag_success,
            "successRate": round(rag_success / rag_total, 4) if rag_total else None,
            "avgTotalMs": round(sum(totals) / len(totals), 0) if totals else 0,
            "p95TotalMs": _percentile(totals, 0.95),
            "avgSearchMs": round(sum(searches) / len(searches), 0) if searches else 0,
            "avgLlmMs": round(sum(llms) / len(llms), 0) if llms else 0,
        },
        "ingestion": {
            "queue": {status: jobs_by_status.get(status, 0) for status in _JOB_STATUSES},
            "windowCompleted": jobs_window.get("SUCCESS", 0),
            "windowDead": jobs_window.get("DEAD", 0),
            "avgJobMs": round(avg_job_ms, 0),
        },
        "documents": docs_by_status,
    }}


@router.get("/timeseries")
async def monitoring_timeseries(
    hours: int = Query(24, ge=1, le=168),
    buckets: int = Query(24, ge=4, le=96),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bucketed RAG runs and finished ingestion jobs for charting."""
    now = _utcnow()
    since = now - timedelta(hours=hours)
    bucket_sec = hours * 3600 / buckets

    rag_rows = (
        await db.execute(
            select(
                RagTraceRun.create_time,
                RagTraceRun.status,
                RagTraceRun.total_duration_ms,
            ).where(
                RagTraceRun.create_time >= since,
                *_tenant_scope(user, RagTraceRun.tenant_id),
            )
        )
    ).all()
    job_rows = (
        await db.execute(
            select(IngestionJob.completed_at, IngestionJob.status).where(
                IngestionJob.deleted == 0,
                IngestionJob.completed_at >= since,
                *_tenant_scope(user, IngestionJob.tenant_id),
            )
        )
    ).all()

    series = [
        {
            "time": (since + timedelta(seconds=bucket_sec * i)).isoformat(
                sep=" ", timespec="seconds"
            ),
            "ragTotal": 0,
            "ragFailed": 0,
            "ragDurations": [],
            "jobsCompleted": 0,
            "jobsDead": 0,
        }
        for i in range(buckets)
    ]

    def _bucket_of(ts: datetime) -> int | None:
        offset = (ts - since).total_seconds()
        if offset < 0:
            return None
        return min(buckets - 1, int(offset / bucket_sec))

    for create_time, status, duration in rag_rows:
        idx = _bucket_of(create_time)
        if idx is None:
            continue
        series[idx]["ragTotal"] += 1
        if status != "success":
            series[idx]["ragFailed"] += 1
        elif duration is not None:
            series[idx]["ragDurations"].append(duration)

    for completed_at, status in job_rows:
        idx = _bucket_of(completed_at)
        if idx is None:
            continue
        if status == "SUCCESS":
            series[idx]["jobsCompleted"] += 1
        elif status == "DEAD":
            series[idx]["jobsDead"] += 1

    for point in series:
        durations = point.pop("ragDurations")
        point["ragAvgMs"] = (
            round(sum(durations) / len(durations), 0) if durations else 0
        )

    return {"code": "0", "message": "success", "data": {
        "windowHours": hours,
        "points": series,
    }}


@router.get("/ingestion-jobs")
async def list_ingestion_jobs(
    status: str = Query("", description="Filter by job status"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Paginated ingestion outbox jobs, most recent first."""
    conditions = [IngestionJob.deleted == 0, *_tenant_scope(user, IngestionJob.tenant_id)]
    if status:
        conditions.append(IngestionJob.status == status.upper())

    total = (
        await db.execute(select(func.count(IngestionJob.id)).where(*conditions))
    ).scalar() or 0
    jobs = (
        (
            await db.execute(
                select(IngestionJob)
                .where(*conditions)
                .order_by(desc(IngestionJob.create_time))
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    doc_ids = {job.doc_id for job in jobs}
    doc_names: dict[str, str] = {}
    if doc_ids:
        rows = (
            await db.execute(
                select(KnowledgeDocument.id, KnowledgeDocument.doc_name).where(
                    KnowledgeDocument.id.in_(doc_ids)
                )
            )
        ).all()
        doc_names = {doc_id: name for doc_id, name in rows}

    return {"code": "0", "message": "success", "data": {
        "total": total,
        "items": [{
            "id": job.id,
            "docId": job.doc_id,
            "docName": doc_names.get(job.doc_id, ""),
            "kbId": job.kb_id,
            "operation": job.operation,
            "status": job.status,
            "attempts": job.attempts,
            "maxAttempts": job.max_attempts,
            "durationMs": job.duration_ms,
            "chunkCount": job.chunk_count,
            "errorMessage": (job.error_message or "")[:300],
            "nextRetryTime": str(job.next_retry_time) if job.next_retry_time else None,
            "createTime": str(job.create_time),
            "completedAt": str(job.completed_at) if job.completed_at else None,
        } for job in jobs],
    }}


@router.post("/ingestion-jobs/{job_id}/retry")
async def retry_ingestion_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Requeue a DEAD or RETRY job for immediate execution."""
    job = (
        await db.execute(
            select(IngestionJob).where(
                IngestionJob.id == job_id,
                IngestionJob.deleted == 0,
                *_tenant_scope(user, IngestionJob.tenant_id),
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status not in ("DEAD", "RETRY"):
        raise HTTPException(status_code=400, detail="仅 DEAD/RETRY 状态的任务可重试")

    job.status = "QUEUED"
    job.attempts = 0
    job.next_retry_time = None
    job.error_message = None
    doc = (
        await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == job.doc_id)
        )
    ).scalar_one_or_none()
    if doc is not None:
        doc.status = "queued"
    await db.flush()
    return {"code": "0", "message": "success", "data": {"id": job.id, "status": job.status}}
