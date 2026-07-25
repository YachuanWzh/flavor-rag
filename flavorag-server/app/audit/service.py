"""Audit logging service — persists business change audit entries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.models import BizChangeLog, gen_id

_log = get_logger("flavorag.audit")


async def record_audit(
    *,
    biz_type: str,
    biz_id: str,
    operation_type: str,
    action_desc: str | None = None,
    before_snapshot: dict | None = None,
    after_snapshot: dict | None = None,
    operator_id: str | None = None,
    operator_name: str | None = None,
    operator_role: str | None = None,
    success: bool = True,
    error_message: str | None = None,
    class_name: str | None = None,
    method_name: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    db: AsyncSession | None = None,
) -> str:
    """Persist an audit log entry.

    Can be called from within an existing DB session (pass *db*),
    or used standalone (uses its own session).

    Returns the new log entry ID.
    """
    # Build change_diff from before/after snapshots
    change_diff: dict[str, Any] | None = None
    if before_snapshot is not None and after_snapshot is not None:
        change_diff = _compute_diff(before_snapshot, after_snapshot)

    entry = BizChangeLog(
        id=gen_id(),
        biz_type=biz_type,
        biz_id=biz_id,
        operation_type=operation_type,
        action_desc=action_desc,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        change_diff=change_diff,
        operator_id=operator_id,
        operator_name=operator_name,
        operator_role=operator_role,
        success=1 if success else 0,
        error_message=error_message,
        class_name=class_name,
        method_name=method_name,
        ip=ip,
        user_agent=user_agent,
        create_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    if db is not None:
        db.add(entry)
        await db.flush()
    else:
        async with async_session_factory() as session:
            session.add(entry)
            await session.commit()

    _log.info(
        "audit_recorded",
        biz_type=biz_type,
        biz_id=biz_id,
        operation=operation_type,
        success=success,
    )
    return entry.id


async def query_audit_logs(
    db: AsyncSession,
    *,
    biz_type: str | None = None,
    biz_id: str | None = None,
    operation_type: str | None = None,
    operator_id: str | None = None,
    success: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Query audit logs with pagination.

    Returns (rows, total).
    """
    from sqlalchemy import desc

    stmt = select(BizChangeLog)
    count_stmt = select(BizChangeLog)

    if biz_type:
        stmt = stmt.where(BizChangeLog.biz_type == biz_type)
        count_stmt = count_stmt.where(BizChangeLog.biz_type == biz_type)
    if biz_id:
        stmt = stmt.where(BizChangeLog.biz_id == biz_id)
        count_stmt = count_stmt.where(BizChangeLog.biz_id == biz_id)
    if operation_type:
        stmt = stmt.where(BizChangeLog.operation_type == operation_type)
        count_stmt = count_stmt.where(BizChangeLog.operation_type == operation_type)
    if operator_id:
        stmt = stmt.where(BizChangeLog.operator_id == operator_id)
        count_stmt = count_stmt.where(BizChangeLog.operator_id == operator_id)
    if success is not None:
        val = 1 if success else 0
        stmt = stmt.where(BizChangeLog.success == val)
        count_stmt = count_stmt.where(BizChangeLog.success == val)

    from sqlalchemy import func
    total_result = await db.execute(select(func.count()).select_from(count_stmt.subquery()))
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    stmt = stmt.order_by(desc(BizChangeLog.create_time)).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "bizType": r.biz_type,
            "bizId": r.biz_id,
            "operationType": r.operation_type,
            "actionDesc": r.action_desc,
            "beforeSnapshot": r.before_snapshot,
            "afterSnapshot": r.after_snapshot,
            "changeDiff": r.change_diff,
            "operatorId": r.operator_id,
            "operatorName": r.operator_name,
            "operatorRole": r.operator_role,
            "success": r.success == 1,
            "errorMessage": r.error_message,
            "className": r.class_name,
            "methodName": r.method_name,
            "ip": r.ip,
            "userAgent": r.user_agent,
            "createTime": str(r.create_time),
        }
        for r in rows
    ], total


def _compute_diff(before: dict | None, after: dict | None) -> dict[str, Any] | None:
    """Compute a simple diff between two dicts."""
    if before is None or after is None:
        return None
    diff: dict[str, Any] = {}
    all_keys = set(before.keys()) | set(after.keys())
    for key in all_keys:
        old = before.get(key)
        new = after.get(key)
        if old != new:
            diff[key] = {"before": old, "after": new}
    return diff if diff else None
