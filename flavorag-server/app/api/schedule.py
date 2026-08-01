"""Document schedule management API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import (
    User,
    KnowledgeDocument,
    KnowledgeDocumentSchedule,
    KnowledgeDocumentScheduleExec,
    gen_id,
)
from app.config.settings import settings
from app.services.schedule.scheduler import calculate_next_run
from app.time_utils import utc_isoformat

router = APIRouter(prefix="/api/admin/schedule", tags=["admin-schedule"])


class ScheduleCreateRequest(BaseModel):
    doc_id: str = Field(..., description="文档ID")
    cron_expr: str = Field(..., description="Cron表达式或间隔秒数")
    enabled: bool = Field(True, description="是否启用")


class ScheduleUpdateRequest(BaseModel):
    cron_expr: str | None = Field(None, description="Cron表达式或间隔秒数")
    enabled: bool | None = Field(None, description="是否启用")


@router.get("/list")
async def list_schedules(
    kb_id: str | None = Query(None, description="知识库ID过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all document schedules."""
    stmt = select(KnowledgeDocumentSchedule)
    if kb_id:
        stmt = stmt.where(KnowledgeDocumentSchedule.kb_id == kb_id)

    from sqlalchemy import func
    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    stmt = stmt.order_by(KnowledgeDocumentSchedule.next_run_time).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return {
        "code": "0",
        "message": "success",
        "data": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "rows": [
                {
                    "id": r.id,
                    "docId": r.doc_id,
                    "kbId": r.kb_id,
                    "cronExpr": r.cron_expr,
                    "enabled": r.enabled == 1,
                    "nextRunTime": utc_isoformat(r.next_run_time),
                    "lastRunTime": utc_isoformat(r.last_run_time),
                    "lastSuccessTime": utc_isoformat(r.last_success_time),
                    "lastStatus": r.last_status,
                    "lastError": r.last_error,
                    "lockOwner": r.lock_owner,
                }
                for r in rows
            ],
            "timezone": settings.app_timezone,
        },
    }


@router.post("/create")
async def create_schedule(
    req: ScheduleCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a schedule for a document."""
    # Validate document exists
    doc_result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == req.doc_id,
            KnowledgeDocument.deleted == 0,
        )
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # Check duplicate
    existing = await db.execute(
        select(KnowledgeDocumentSchedule).where(
            KnowledgeDocumentSchedule.doc_id == req.doc_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该文档已有调度配置")

    try:
        next_run = calculate_next_run(req.cron_expr)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sched = KnowledgeDocumentSchedule(
        id=gen_id(),
        doc_id=req.doc_id,
        kb_id=doc.kb_id,
        cron_expr=req.cron_expr,
        enabled=1 if req.enabled else 0,
        next_run_time=next_run,
    )
    db.add(sched)

    # Update document schedule fields
    doc.schedule_enabled = 1 if req.enabled else 0
    doc.schedule_cron = req.cron_expr

    await db.flush()

    return {
        "code": "0",
        "message": "success",
        "data": {"id": sched.id},
    }


@router.put("/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    req: ScheduleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a schedule configuration."""
    result = await db.execute(
        select(KnowledgeDocumentSchedule).where(
            KnowledgeDocumentSchedule.id == schedule_id,
        )
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="调度配置不存在")

    if req.cron_expr is not None:
        sched.cron_expr = req.cron_expr
        try:
            sched.next_run_time = calculate_next_run(req.cron_expr)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if req.enabled is not None:
        sched.enabled = 1 if req.enabled else 0

    await db.flush()

    return {"code": "0", "message": "success", "data": None}


@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a schedule."""
    result = await db.execute(
        select(KnowledgeDocumentSchedule).where(
            KnowledgeDocumentSchedule.id == schedule_id,
        )
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="调度配置不存在")

    # Also disable schedule on the document
    doc_result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == sched.doc_id,
            KnowledgeDocument.deleted == 0,
        )
    )
    doc = doc_result.scalar_one_or_none()
    if doc:
        doc.schedule_enabled = 0
        doc.schedule_cron = None

    await db.delete(sched)
    await db.flush()

    return {"code": "0", "message": "success", "data": None}


@router.get("/executions")
async def list_executions(
    schedule_id: str | None = Query(None, description="调度ID过滤"),
    doc_id: str | None = Query(None, description="文档ID过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List schedule execution history."""
    from sqlalchemy import func, desc

    stmt = select(KnowledgeDocumentScheduleExec)
    if schedule_id:
        stmt = stmt.where(KnowledgeDocumentScheduleExec.schedule_id == schedule_id)
    if doc_id:
        stmt = stmt.where(KnowledgeDocumentScheduleExec.doc_id == doc_id)

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    stmt = stmt.order_by(desc(KnowledgeDocumentScheduleExec.start_time)).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return {
        "code": "0",
        "message": "success",
        "data": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "rows": [
                {
                    "id": r.id,
                    "scheduleId": r.schedule_id,
                    "docId": r.doc_id,
                    "kbId": r.kb_id,
                    "status": r.status,
                    "message": r.message,
                    "startTime": utc_isoformat(r.start_time),
                    "endTime": utc_isoformat(r.end_time),
                    "fileName": r.file_name,
                    "fileSize": r.file_size,
                }
                for r in rows
            ],
        },
    }
