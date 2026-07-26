"""Enterprise ingestion management, execution and observability APIs."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_db
from app.ingestion.pipeline_engine import IngestionEngine, validate_pipeline_graph
from app.models import (
    IndexSyncJob,
    IngestionPipeline as PipelineModel,
    IngestionPipelineNode as PipelineNodeModel,
    IngestionTask,
    IngestionTaskNode,
    KnowledgeBase,
    KnowledgeDocumentScheduleExec,
    User,
    gen_id,
)

router = APIRouter(prefix="/api/admin/ingestion", tags=["admin-ingestion"])


def _tenant(user: User) -> str:
    return user.tenant_id or "default"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NodeDef(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)
    node_type: str
    next_node_id: str | None = None
    settings_json: dict | None = None
    condition_json: dict | None = None


class PipelineCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=512)
    nodes: list[NodeDef] = Field(default_factory=list)


class PipelineUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=512)
    enabled: bool | None = None
    nodes: list[NodeDef] | None = None


class TaskExecuteRequest(BaseModel):
    pipeline_id: str
    source_type: str
    source_location: str
    source_file_name: str = ""
    kb_id: str = ""
    doc_id: str = ""
    idempotency_key: str | None = Field(None, max_length=128)
    sla_ms: int = Field(default=300_000, ge=1_000, le=3_600_000)


def _task_duration(task: IngestionTask) -> int:
    if task.total_duration_ms is not None:
        return int(task.total_duration_ms)
    if task.started_at and task.completed_at:
        return max(
            0,
            int((task.completed_at - task.started_at).total_seconds() * 1000),
        )
    return 0


def _task_payload(task: IngestionTask, pipeline_name: str = "") -> dict:
    return {
        "id": task.id,
        "tenantId": task.tenant_id,
        "pipelineId": task.pipeline_id,
        "pipelineName": pipeline_name,
        "kbId": task.kb_id,
        "docId": task.doc_id,
        "traceId": task.trace_id,
        "parentTaskId": task.parent_task_id,
        "attempt": task.attempt or 1,
        "sourceType": task.source_type,
        "sourceFileName": task.source_file_name,
        "status": task.status,
        "chunkCount": task.chunk_count or 0,
        "durationMs": _task_duration(task),
        "slaMs": task.sla_ms or 300_000,
        "slaBreached": _task_duration(task) > (task.sla_ms or 300_000),
        "errorMessage": task.error_message,
        "heartbeatAt": task.heartbeat_at.isoformat() if task.heartbeat_at else None,
        "startedAt": task.started_at.isoformat() if task.started_at else None,
        "completedAt": task.completed_at.isoformat() if task.completed_at else None,
        "createTime": task.create_time.isoformat() if task.create_time else None,
    }


async def _pipeline_names(
    db: AsyncSession,
    tenant_id: str,
) -> dict[str, str]:
    rows = (
        await db.execute(
            select(PipelineModel.id, PipelineModel.name).where(
                PipelineModel.tenant_id == tenant_id,
                PipelineModel.deleted == 0,
            )
        )
    ).all()
    return {pipeline_id: name for pipeline_id, name in rows}


@router.get("/pipelines")
async def list_pipelines(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    statement = select(PipelineModel).where(
        PipelineModel.tenant_id == _tenant(user),
        PipelineModel.deleted == 0,
    )
    total = (
        await db.execute(select(func.count()).select_from(statement.subquery()))
    ).scalar() or 0
    rows = (
        await db.execute(
            statement.order_by(desc(PipelineModel.create_time))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    task_rows = (
        await db.execute(
            select(IngestionTask)
            .where(
                IngestionTask.tenant_id == _tenant(user),
                IngestionTask.deleted == 0,
                IngestionTask.create_time >= _utcnow() - timedelta(days=7),
            )
            .order_by(IngestionTask.create_time)
        )
    ).scalars().all()
    by_pipeline: dict[str, list[IngestionTask]] = defaultdict(list)
    for task in task_rows:
        by_pipeline[task.pipeline_id].append(task)
    payload = []
    for pipeline in rows:
        recent = by_pipeline[pipeline.id]
        completed = [task for task in recent if task.status in {"success", "error", "timeout"}]
        successes = sum(task.status == "success" for task in completed)
        payload.append(
            {
                "id": pipeline.id,
                "name": pipeline.name,
                "description": pipeline.description,
                "enabled": bool(pipeline.enabled),
                "createdBy": pipeline.created_by,
                "createTime": pipeline.create_time.isoformat()
                if pipeline.create_time
                else None,
                "health": {
                    "runs7d": len(recent),
                    "successRate": successes / len(completed) if completed else None,
                    "lastStatus": recent[-1].status if recent else None,
                },
            }
        )
    return {
        "code": "0",
        "data": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "rows": payload,
        },
    }


@router.post("/pipelines")
async def create_pipeline(
    request: PipelineCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    validate_pipeline_graph(request.nodes)
    pipeline = PipelineModel(
        id=gen_id(),
        tenant_id=_tenant(user),
        name=request.name.strip(),
        description=request.description or "",
        enabled=1,
        created_by=user.id,
    )
    db.add(pipeline)
    for node in request.nodes:
        db.add(
            PipelineNodeModel(
                id=gen_id(),
                pipeline_id=pipeline.id,
                node_id=node.node_id,
                node_type=node.node_type,
                next_node_id=node.next_node_id,
                settings_json=node.settings_json,
                condition_json=node.condition_json,
                created_by=user.id,
            )
        )
    await db.flush()
    return {"code": "0", "data": {"id": pipeline.id}}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(
    pipeline_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pipeline = (
        await db.execute(
            select(PipelineModel).where(
                PipelineModel.id == pipeline_id,
                PipelineModel.tenant_id == _tenant(user),
                PipelineModel.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if pipeline is None:
        raise HTTPException(status_code=404, detail="流水线不存在")
    nodes = (
        await db.execute(
            select(PipelineNodeModel)
            .where(
                PipelineNodeModel.pipeline_id == pipeline_id,
                PipelineNodeModel.deleted == 0,
            )
            .order_by(PipelineNodeModel.create_time)
        )
    ).scalars().all()
    return {
        "code": "0",
        "data": {
            "id": pipeline.id,
            "name": pipeline.name,
            "description": pipeline.description,
            "enabled": bool(pipeline.enabled),
            "createTime": pipeline.create_time.isoformat()
            if pipeline.create_time
            else None,
            "nodes": [
                {
                    "id": node.id,
                    "nodeId": node.node_id,
                    "nodeType": node.node_type,
                    "nextNodeId": node.next_node_id,
                    "settingsJson": node.settings_json,
                    "conditionJson": node.condition_json,
                }
                for node in nodes
            ],
        },
    }


@router.put("/pipelines/{pipeline_id}")
async def update_pipeline(
    pipeline_id: str,
    request: PipelineUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pipeline = (
        await db.execute(
            select(PipelineModel).where(
                PipelineModel.id == pipeline_id,
                PipelineModel.tenant_id == _tenant(user),
                PipelineModel.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if pipeline is None:
        raise HTTPException(status_code=404, detail="流水线不存在")
    if request.name is not None:
        pipeline.name = request.name.strip()
    if request.description is not None:
        pipeline.description = request.description
    if request.enabled is not None:
        pipeline.enabled = int(request.enabled)
    if request.nodes is not None:
        validate_pipeline_graph(request.nodes)
        current = (
            await db.execute(
                select(PipelineNodeModel).where(
                    PipelineNodeModel.pipeline_id == pipeline_id,
                    PipelineNodeModel.deleted == 0,
                )
            )
        ).scalars().all()
        for node in current:
            node.deleted = 1
        for node in request.nodes:
            db.add(
                PipelineNodeModel(
                    id=gen_id(),
                    pipeline_id=pipeline_id,
                    node_id=node.node_id,
                    node_type=node.node_type,
                    next_node_id=node.next_node_id,
                    settings_json=node.settings_json,
                    condition_json=node.condition_json,
                    created_by=user.id,
                )
            )
    pipeline.updated_by = user.id
    await db.flush()
    return {"code": "0", "data": None}


@router.delete("/pipelines/{pipeline_id}")
async def delete_pipeline(
    pipeline_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pipeline = (
        await db.execute(
            select(PipelineModel).where(
                PipelineModel.id == pipeline_id,
                PipelineModel.tenant_id == _tenant(user),
                PipelineModel.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if pipeline is None:
        raise HTTPException(status_code=404, detail="流水线不存在")
    active = (
        await db.execute(
            select(func.count(IngestionTask.id)).where(
                IngestionTask.pipeline_id == pipeline_id,
                IngestionTask.tenant_id == _tenant(user),
                IngestionTask.status == "running",
                IngestionTask.deleted == 0,
            )
        )
    ).scalar() or 0
    if active:
        raise HTTPException(status_code=409, detail="流水线仍有运行中的任务")
    pipeline.deleted = 1
    await db.flush()
    return {"code": "0", "data": None}


async def _run_task(
    request: TaskExecuteRequest,
    db: AsyncSession,
    user: User,
    *,
    parent_task: IngestionTask | None = None,
):
    if request.idempotency_key:
        existing = (
            await db.execute(
                select(IngestionTask).where(
                    IngestionTask.tenant_id == _tenant(user),
                    IngestionTask.idempotency_key == request.idempotency_key,
                    IngestionTask.deleted == 0,
                )
            )
        ).scalar_one_or_none()
        if existing:
            names = await _pipeline_names(db, _tenant(user))
            return {
                **_task_payload(existing, names.get(existing.pipeline_id, "")),
                "deduplicated": True,
                "nodes": [],
            }
    result = await IngestionEngine().execute_pipeline(
        pipeline_id=request.pipeline_id,
        source_type=request.source_type,
        source_location=request.source_location,
        source_file_name=request.source_file_name,
        kb_id=request.kb_id,
        doc_id=request.doc_id,
        user_id=user.id,
        tenant_id=_tenant(user),
        idempotency_key=request.idempotency_key,
        parent_task_id=parent_task.id if parent_task else None,
        attempt=(parent_task.attempt or 1) + 1 if parent_task else 1,
        sla_ms=request.sla_ms,
        db=db,
    )
    task = (
        await db.execute(select(IngestionTask).where(IngestionTask.id == result.task_id))
    ).scalar_one()
    names = await _pipeline_names(db, _tenant(user))
    return {
        **_task_payload(task, names.get(task.pipeline_id, "")),
        "deduplicated": False,
        "nodes": [
            {
                "nodeId": node.node_id,
                "nodeType": node.node_type,
                "status": node.status,
                "durationMs": node.duration_ms,
                "message": node.message,
                "errorMessage": node.error_message,
            }
            for node in result.node_results
        ],
    }


@router.post("/tasks/execute")
async def execute_task(
    request: TaskExecuteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {"code": "0", "data": await _run_task(request, db, user)}


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = (
        await db.execute(
            select(IngestionTask).where(
                IngestionTask.id == task_id,
                IngestionTask.tenant_id == _tenant(user),
                IngestionTask.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == "running":
        raise HTTPException(status_code=409, detail="运行中的任务不能重试")
    request = TaskExecuteRequest(
        pipeline_id=task.pipeline_id,
        source_type=task.source_type,
        source_location=task.source_location,
        source_file_name=task.source_file_name or "",
        kb_id=task.kb_id or "",
        doc_id=task.doc_id or "",
        sla_ms=task.sla_ms or 300_000,
    )
    return {"code": "0", "data": await _run_task(request, db, user, parent_task=task)}


@router.get("/tasks")
async def list_tasks(
    pipeline_id: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    statement = select(IngestionTask).where(
        IngestionTask.tenant_id == _tenant(user),
        IngestionTask.deleted == 0,
    )
    if pipeline_id:
        statement = statement.where(IngestionTask.pipeline_id == pipeline_id)
    if status:
        statement = statement.where(IngestionTask.status == status)
    total = (
        await db.execute(select(func.count()).select_from(statement.subquery()))
    ).scalar() or 0
    rows = (
        await db.execute(
            statement.order_by(desc(IngestionTask.create_time))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    names = await _pipeline_names(db, _tenant(user))
    return {
        "code": "0",
        "data": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "rows": [
                _task_payload(task, names.get(task.pipeline_id, ""))
                for task in rows
            ],
        },
    }


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = (
        await db.execute(
            select(IngestionTask).where(
                IngestionTask.id == task_id,
                IngestionTask.tenant_id == _tenant(user),
                IngestionTask.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    nodes = (
        await db.execute(
            select(IngestionTaskNode)
            .where(
                IngestionTaskNode.task_id == task_id,
                IngestionTaskNode.deleted == 0,
            )
            .order_by(IngestionTaskNode.node_order)
        )
    ).scalars().all()
    names = await _pipeline_names(db, _tenant(user))
    return {
        "code": "0",
        "data": {
            **_task_payload(task, names.get(task.pipeline_id, "")),
            "sourceLocation": task.source_location,
            "logs": task.logs_json or {},
            "nodes": [
                {
                    "id": node.id,
                    "nodeId": node.node_id,
                    "nodeType": node.node_type,
                    "nodeOrder": node.node_order,
                    "attempt": node.attempt or 1,
                    "status": node.status,
                    "durationMs": node.duration_ms or 0,
                    "message": node.message,
                    "errorMessage": node.error_message,
                    "startedAt": node.started_at.isoformat()
                    if node.started_at
                    else None,
                    "completedAt": node.completed_at.isoformat()
                    if node.completed_at
                    else None,
                }
                for node in nodes
            ],
        },
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _failure_type(message: str | None) -> str:
    text = (message or "").lower()
    patterns = (
        ("timeout", r"timeout|timed out|超时"),
        ("network", r"http|network|connect|dns|socket|url"),
        ("parse", r"parse|parser|ocr|解析"),
        ("embedding", r"embed|vector|向量"),
        ("index", r"milvus|elastic|index|neo4j|索引"),
        ("validation", r"invalid|not found|不存在|不支持|循环|重复"),
        ("storage", r"s3|storage|file|磁盘|文件"),
    )
    for name, pattern in patterns:
        if re.search(pattern, text):
            return name
    return "unknown"


@router.get("/monitor/overview")
async def monitor_overview(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tenant_id = _tenant(user)
    now = _utcnow()
    since = now - timedelta(days=7)
    tasks = list(
        (
            await db.execute(
                select(IngestionTask)
                .where(
                    IngestionTask.tenant_id == tenant_id,
                    IngestionTask.deleted == 0,
                    IngestionTask.create_time >= since,
                )
                .order_by(IngestionTask.create_time)
                .limit(5000)
            )
        ).scalars().all()
    )
    last_24h = [
        task for task in tasks
        if task.create_time and task.create_time >= now - timedelta(hours=24)
    ]
    terminal = [
        task for task in last_24h
        if task.status in {"success", "error", "timeout"}
    ]
    successes = [task for task in terminal if task.status == "success"]
    durations = [_task_duration(task) for task in terminal if _task_duration(task)]
    stuck = [
        task
        for task in tasks
        if task.status == "running"
        and (task.heartbeat_at or task.started_at or task.create_time)
        < now - timedelta(minutes=15)
    ]
    sla_breaches = [
        task
        for task in terminal
        if _task_duration(task) > (task.sla_ms or 300_000)
    ]
    names = await _pipeline_names(db, tenant_id)

    task_ids = [task.id for task in tasks]
    nodes = []
    if task_ids:
        nodes = list(
            (
                await db.execute(
                    select(IngestionTaskNode).where(
                        IngestionTaskNode.task_id.in_(task_ids),
                        IngestionTaskNode.deleted == 0,
                    )
                )
            ).scalars().all()
        )
    node_groups: dict[str, list[IngestionTaskNode]] = defaultdict(list)
    for node in nodes:
        node_groups[node.node_type].append(node)
    node_stats = []
    for node_type, items in node_groups.items():
        node_durations = [int(item.duration_ms or 0) for item in items]
        node_stats.append(
            {
                "nodeType": node_type,
                "runs": len(items),
                "errors": sum(item.status == "error" for item in items),
                "errorRate": sum(item.status == "error" for item in items) / len(items),
                "avgDurationMs": round(mean(node_durations)) if node_durations else 0,
                "p95DurationMs": _percentile(node_durations, 0.95),
                "retryRate": sum((item.attempt or 1) > 1 for item in items) / len(items),
            }
        )
    node_stats.sort(key=lambda item: item["p95DurationMs"], reverse=True)

    failure_counts = Counter(
        _failure_type(task.error_message)
        for task in terminal
        if task.status in {"error", "timeout"}
    )
    buckets = []
    for hours_ago in range(23, -1, -1):
        bucket_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(
            hours=hours_ago
        )
        bucket_end = bucket_start + timedelta(hours=1)
        bucket_tasks = [
            task
            for task in last_24h
            if task.create_time and bucket_start <= task.create_time < bucket_end
        ]
        bucket_terminal = [
            task
            for task in bucket_tasks
            if task.status in {"success", "error", "timeout"}
        ]
        buckets.append(
            {
                "timestamp": bucket_start.isoformat(),
                "total": len(bucket_tasks),
                "success": sum(task.status == "success" for task in bucket_terminal),
                "error": sum(task.status in {"error", "timeout"} for task in bucket_terminal),
                "chunks": sum(task.chunk_count or 0 for task in bucket_tasks),
                "p95DurationMs": _percentile(
                    [_task_duration(task) for task in bucket_terminal if _task_duration(task)],
                    0.95,
                ),
            }
        )

    index_jobs = list(
        (
            await db.execute(
                select(IndexSyncJob).where(
                    IndexSyncJob.tenant_id == tenant_id,
                    IndexSyncJob.deleted == 0,
                    IndexSyncJob.status.in_(["PENDING", "RUNNING", "RETRY", "FAILED"]),
                )
            )
        ).scalars().all()
    )
    schedule_errors = (
        await db.execute(
            select(KnowledgeDocumentScheduleExec)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocumentScheduleExec.kb_id)
            .where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeDocumentScheduleExec.status == "error",
                KnowledgeDocumentScheduleExec.create_time >= now - timedelta(hours=24),
            )
            .order_by(desc(KnowledgeDocumentScheduleExec.create_time))
            .limit(20)
        )
    ).scalars().all()
    incidents = [
        {
            "id": task.id,
            "kind": "stuck",
            "severity": "critical",
            "title": f"{names.get(task.pipeline_id, task.pipeline_id)} 任务心跳中断",
            "detail": task.source_file_name or task.id,
            "timestamp": (
                task.heartbeat_at or task.started_at or task.create_time
            ).isoformat(),
            "taskId": task.id,
        }
        for task in stuck
    ]
    incidents.extend(
        {
            "id": task.id,
            "kind": "failure",
            "severity": "critical" if task.attempt and task.attempt > 1 else "warning",
            "title": f"{names.get(task.pipeline_id, task.pipeline_id)} 执行失败",
            "detail": (task.error_message or "未知错误")[:180],
            "timestamp": (task.completed_at or task.create_time).isoformat(),
            "taskId": task.id,
        }
        for task in reversed(terminal)
        if task.status in {"error", "timeout"}
    )
    incidents.extend(
        {
            "id": job.id,
            "kind": "index_sync",
            "severity": "critical" if job.status == "FAILED" else "warning",
            "title": "外部索引一致性待恢复",
            "detail": job.last_error or job.status,
            "timestamp": job.create_time.isoformat() if job.create_time else None,
            "taskId": None,
        }
        for job in index_jobs
    )

    success_rate = len(successes) / len(terminal) if terminal else 1.0
    health = (
        "critical"
        if stuck or any(job.status == "FAILED" for job in index_jobs)
        else "warning"
        if success_rate < 0.95 or index_jobs or schedule_errors
        else "healthy"
    )
    return {
        "code": "0",
        "data": {
            "health": health,
            "generatedAt": now.isoformat(),
            "summary": {
                "total24h": len(last_24h),
                "running": sum(task.status == "running" for task in tasks),
                "successRate": success_rate,
                "p50DurationMs": _percentile(durations, 0.50),
                "p95DurationMs": _percentile(durations, 0.95),
                "slaBreachRate": len(sla_breaches) / len(terminal) if terminal else 0.0,
                "chunks24h": sum(task.chunk_count or 0 for task in last_24h),
                "stuckTasks": len(stuck),
                "indexBacklog": len(index_jobs),
                "scheduleErrors": len(schedule_errors),
                "retryRecoveryRate": (
                    sum(task.status == "success" and (task.attempt or 1) > 1 for task in terminal)
                    / sum((task.attempt or 1) > 1 for task in terminal)
                    if any((task.attempt or 1) > 1 for task in terminal)
                    else 1.0
                ),
            },
            "trend": buckets,
            "nodeStats": node_stats,
            "failureReasons": [
                {"reason": reason, "count": count}
                for reason, count in failure_counts.most_common()
            ],
            "incidents": incidents[:30],
            "recentTasks": [
                _task_payload(task, names.get(task.pipeline_id, ""))
                for task in reversed(tasks[-20:])
            ],
        },
    }
