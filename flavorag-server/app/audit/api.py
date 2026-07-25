"""Audit API — admin-only query endpoint for business change logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.auth.dependencies import get_current_user, get_admin_user
from app.models import User
from app.audit.service import query_audit_logs

router = APIRouter(prefix="/api/admin/audit", tags=["admin-audit"])


@router.get("/logs")
async def list_audit_logs(
    biz_type: str | None = Query(None, description="业务类型"),
    biz_id: str | None = Query(None, description="业务对象ID"),
    operation_type: str | None = Query(None, description="操作类型：CREATE/UPDATE/DELETE"),
    operator_id: str | None = Query(None, description="操作人ID"),
    success: bool | None = Query(None, description="是否成功"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """Query business change audit logs (admin only)."""
    rows, total = await query_audit_logs(
        db,
        biz_type=biz_type,
        biz_id=biz_id,
        operation_type=operation_type,
        operator_id=operator_id,
        success=success,
        page=page,
        page_size=page_size,
    )
    return {
        "code": "0",
        "message": "success",
        "data": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "rows": rows,
        },
    }
