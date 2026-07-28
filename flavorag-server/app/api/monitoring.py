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

from app.auth.dependencies import get_admin_user
from app.database.session import get_db
from app.models import (
    BizChangeLog,
    IngestionJob,
    IngestionTask,
    KnowledgeDocument,
    RagTraceRun,
    User,
)

router = APIRouter(prefix="/api/admin/monitoring", tags=["monitoring"])

_JOB_STATUSES = ("QUEUED", "RUNNING", "RETRY", "SUCCESS", "DEAD")
_TASK_STATUS_MAP = {
    "pending": "QUEUED",
    "queued": "QUEUED",
    "running": "RUNNING",
    "retry": "RETRY",
    "success": "SUCCESS",
    "completed": "SUCCESS",
    "failed": "DEAD",
    "error": "DEAD",
    "dead": "DEAD",
}


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


def _task_status(status: str | None) -> str:
    return _TASK_STATUS_MAP.get((status or "").lower(), (status or "UNKNOWN").upper())


@router.get("/summary")
async def monitoring_summary(
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
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

    task_rows = (
        await db.execute(
            select(IngestionTask.status, func.count(IngestionTask.id))
            .where(
                IngestionTask.deleted == 0,
                *_tenant_scope(user, IngestionTask.tenant_id),
            )
            .group_by(IngestionTask.status)
        )
    ).all()
    tasks_by_status: dict[str, int] = {}
    for status, count in task_rows:
        normalized = _task_status(status)
        tasks_by_status[normalized] = tasks_by_status.get(normalized, 0) + count
    combined_queue = {
        status: jobs_by_status.get(status, 0) + tasks_by_status.get(status, 0)
        for status in _JOB_STATUSES
    }

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
    task_window_rows = (
        await db.execute(
            select(IngestionTask.status, func.count(IngestionTask.id))
            .where(
                IngestionTask.deleted == 0,
                IngestionTask.completed_at >= since,
                *_tenant_scope(user, IngestionTask.tenant_id),
            )
            .group_by(IngestionTask.status)
        )
    ).all()
    tasks_window: dict[str, int] = {}
    for status, count in task_window_rows:
        normalized = _task_status(status)
        tasks_window[normalized] = tasks_window.get(normalized, 0) + count

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

    error_count = (
        await db.execute(
            select(func.count(BizChangeLog.id)).where(
                BizChangeLog.biz_type == "system_error",
                BizChangeLog.create_time >= since,
            )
        )
    ).scalar() or 0
    latest_error = (
        await db.execute(
            select(BizChangeLog)
            .where(
                BizChangeLog.biz_type == "system_error",
                BizChangeLog.create_time >= since,
            )
            .order_by(desc(BizChangeLog.create_time))
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_dead_job = (
        await db.execute(
            select(IngestionJob)
            .where(
                IngestionJob.deleted == 0,
                IngestionJob.status == "DEAD",
                *_tenant_scope(user, IngestionJob.tenant_id),
            )
            .order_by(desc(IngestionJob.create_time))
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_failed_task = (
        await db.execute(
            select(IngestionTask)
            .where(
                IngestionTask.deleted == 0,
                func.lower(IngestionTask.status).in_(("failed", "error", "dead")),
                *_tenant_scope(user, IngestionTask.tenant_id),
            )
            .order_by(desc(IngestionTask.create_time))
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_failed_rag = (
        await db.execute(
            select(RagTraceRun)
            .where(
                RagTraceRun.create_time >= since,
                RagTraceRun.status != "success",
                *_tenant_scope(user, RagTraceRun.tenant_id),
            )
            .order_by(desc(RagTraceRun.create_time))
            .limit(1)
        )
    ).scalar_one_or_none()

    diagnostics: list[dict] = []
    dead_total = combined_queue.get("DEAD", 0)
    if dead_total:
        failed_ingestion = (
            latest_dead_job if latest_dead_job is not None else latest_failed_task
        )
        diagnostics.append({
            "key": "ingestion_dead",
            "severity": "critical",
            "title": f"{dead_total} 个摄取任务需要处理",
            "reason": (
                (getattr(failed_ingestion, "error_message", None) or "任务已超过自动重试上限")
                if failed_ingestion else "任务已超过自动重试上限"
            )[:500],
            "action": "打开下方任务详情核对失败节点；异步任务可直接重新入队。",
            "lastOccurredAt": str(getattr(failed_ingestion, "completed_at", None) or getattr(failed_ingestion, "create_time", "")),
        })
    if rag_total - rag_success:
        diagnostics.append({
            "key": "rag_failures",
            "severity": "warning",
            "title": f"窗口内有 {rag_total - rag_success} 次问答未成功",
            "reason": (
                (latest_failed_rag.error_message or latest_failed_rag.rejection_reason)
                if latest_failed_rag else "未找到可用证据或模型调用失败"
            ) or "未找到可用证据或模型调用失败",
            "action": "前往链路追踪按节点查看检索通道、模型与耗时。",
            "lastOccurredAt": str(latest_failed_rag.create_time) if latest_failed_rag else "",
        })
    if error_count:
        diagnostics.append({
            "key": "system_errors",
            "severity": "critical",
            "title": f"窗口内已审计 {error_count} 个系统错误",
            "reason": latest_error.error_message if latest_error else "系统组件报告异常",
            "action": "前往审计日志，筛选“系统错误”查看错误编号、上下文与堆栈。",
            "lastOccurredAt": str(latest_error.create_time) if latest_error else "",
        })
    if not diagnostics:
        diagnostics.append({
            "key": "healthy",
            "severity": "healthy",
            "title": "当前没有需要人工处理的异常",
            "reason": "所选时间窗口内未发现失败问答、死信摄取任务或系统错误。",
            "action": "无需操作；页面每 30 秒自动刷新。",
            "lastOccurredAt": "",
        })

    from app.config.settings import settings
    from app.observability.otel import otel_active

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
            "queue": combined_queue,
            "sources": {
                "asyncOutbox": {status: jobs_by_status.get(status, 0) for status in _JOB_STATUSES},
                "pipelineTasks": {status: tasks_by_status.get(status, 0) for status in _JOB_STATUSES},
            },
            "windowCompleted": jobs_window.get("SUCCESS", 0) + tasks_window.get("SUCCESS", 0),
            "windowDead": jobs_window.get("DEAD", 0) + tasks_window.get("DEAD", 0),
            "avgJobMs": round(avg_job_ms, 0),
        },
        "documents": docs_by_status,
        "diagnostics": diagnostics,
        "errors": {"windowTotal": error_count},
        "observability": {
            "prometheus": {
                "enabled": settings.metrics_enabled,
                "metricsEndpoint": "/metrics",
                "uiUrl": settings.prometheus_ui_url,
            },
            "grafana": {"uiUrl": settings.grafana_ui_url},
            "otel": {
                "enabled": settings.otel_enabled,
                "active": otel_active(),
                "exporterEndpoint": settings.otel_exporter_otlp_endpoint,
                "serviceName": settings.otel_service_name,
                "jaegerUrl": settings.jaeger_ui_url,
            },
        },
    }}


@router.get("/timeseries")
async def monitoring_timeseries(
    hours: int = Query(24, ge=1, le=168),
    buckets: int = Query(24, ge=4, le=96),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
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
    task_rows = (
        await db.execute(
            select(IngestionTask.completed_at, IngestionTask.status).where(
                IngestionTask.deleted == 0,
                IngestionTask.completed_at >= since,
                *_tenant_scope(user, IngestionTask.tenant_id),
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
    for completed_at, status in task_rows:
        idx = _bucket_of(completed_at)
        if idx is None:
            continue
        normalized = _task_status(status)
        if normalized == "SUCCESS":
            series[idx]["jobsCompleted"] += 1
        elif normalized == "DEAD":
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
    user: User = Depends(get_admin_user),
):
    """Unified recent ingestion activity from outbox and visual pipeline tasks."""
    normalized_filter = status.upper()
    job_conditions = [
        IngestionJob.deleted == 0,
        *_tenant_scope(user, IngestionJob.tenant_id),
    ]
    task_conditions = [
        IngestionTask.deleted == 0,
        *_tenant_scope(user, IngestionTask.tenant_id),
    ]
    if normalized_filter:
        job_conditions.append(IngestionJob.status == normalized_filter)
        task_values = [
            raw for raw, normalized in _TASK_STATUS_MAP.items()
            if normalized == normalized_filter
        ]
        task_conditions.append(func.lower(IngestionTask.status).in_(task_values or [""]))

    job_total = (
        await db.execute(select(func.count(IngestionJob.id)).where(*job_conditions))
    ).scalar() or 0
    task_total = (
        await db.execute(select(func.count(IngestionTask.id)).where(*task_conditions))
    ).scalar() or 0
    fetch_limit = min(page * limit, 1000)
    jobs = list(
        (
            await db.execute(
                select(IngestionJob)
                .where(*job_conditions)
                .order_by(desc(IngestionJob.create_time))
                .limit(fetch_limit)
            )
        ).scalars().all()
    )
    tasks = list(
        (
            await db.execute(
                select(IngestionTask)
                .where(*task_conditions)
                .order_by(desc(IngestionTask.create_time))
                .limit(fetch_limit)
            )
        ).scalars().all()
    )
    doc_ids = {
        item.doc_id for item in [*jobs, *tasks] if getattr(item, "doc_id", None)
    }
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

    items = [{
            "id": job.id,
            "source": "async_outbox",
            "sourceLabel": "异步队列",
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
            "retryable": job.status in ("DEAD", "RETRY"),
            "detail": {
                "idempotencyKey": job.idempotency_key,
                "generation": job.generation,
                "filePath": job.file_path,
            },
        } for job in jobs]
    items.extend({
        "id": task.id,
        "source": "pipeline_task",
        "sourceLabel": "流水线",
        "docId": task.doc_id or "",
        "docName": doc_names.get(task.doc_id or "", task.source_file_name or ""),
        "kbId": task.kb_id or "",
        "operation": "INGEST",
        "status": _task_status(task.status),
        "attempts": task.attempt,
        "maxAttempts": task.attempt,
        "durationMs": task.total_duration_ms,
        "chunkCount": task.chunk_count,
        "errorMessage": (task.error_message or "")[:300],
        "nextRetryTime": None,
        "createTime": str(task.create_time),
        "completedAt": str(task.completed_at) if task.completed_at else None,
        "retryable": False,
        "detail": {
            "pipelineId": task.pipeline_id,
            "traceId": task.trace_id,
            "sourceType": task.source_type,
            "sourceLocation": task.source_location,
            "logs": task.logs_json or [],
        },
    } for task in tasks)
    items.sort(key=lambda item: item["createTime"], reverse=True)
    start = (page - 1) * limit

    return {"code": "0", "message": "success", "data": {
        "total": job_total + task_total,
        "items": items[start:start + limit],
    }}


@router.post("/ingestion-jobs/{job_id}/retry")
async def retry_ingestion_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
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
