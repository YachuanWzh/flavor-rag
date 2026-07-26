"""Sample question management API — CRUD for SampleQuestion."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, SampleQuestion, gen_id

router = APIRouter(prefix="/api/admin/sample-questions", tags=["sample-questions"])


class SampleQuestionCreate(BaseModel):
    question: str = Field(..., max_length=512)
    kb_id: str | None = None
    sort_order: int = 0
    enabled: int = 1


class SampleQuestionUpdate(BaseModel):
    question: str | None = None
    sort_order: int | None = None
    enabled: int | None = None


@router.get("")
async def list_questions(
    kb_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all sample questions, optionally filtered by kb_id."""
    q = select(SampleQuestion).where(
        SampleQuestion.deleted == 0,
        SampleQuestion.tenant_id == (user.tenant_id or "default"),
    )
    if kb_id:
        q = q.where(SampleQuestion.kb_id == kb_id)
    q = q.order_by(SampleQuestion.sort_order, SampleQuestion.create_time)

    result = await db.execute(q)
    items = result.scalars().all()

    return {"code": "0", "message": "success", "data": [
        {
            "id": s.id,
            "question": s.question,
            "kbId": s.kb_id,
            "sortOrder": s.sort_order,
            "enabled": s.enabled,
            "createTime": str(s.create_time),
        }
        for s in items
    ]}


@router.post("")
async def create_question(
    req: SampleQuestionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new sample question."""
    sq = SampleQuestion(
        id=gen_id(),
        tenant_id=user.tenant_id or "default",
        question=req.question,
        kb_id=req.kb_id,
        sort_order=req.sort_order,
        enabled=req.enabled,
    )
    db.add(sq)
    await db.flush()
    return {"code": "0", "message": "success", "data": {"id": sq.id}}


@router.put("/{question_id}")
async def update_question(
    question_id: str,
    req: SampleQuestionUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a sample question."""
    result = await db.execute(
        select(SampleQuestion).where(
            SampleQuestion.id == question_id,
            SampleQuestion.tenant_id == (user.tenant_id or "default"),
            SampleQuestion.deleted == 0,
        )
    )
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(status_code=404, detail="示例问题不存在")

    if req.question is not None:
        sq.question = req.question
    if req.sort_order is not None:
        sq.sort_order = req.sort_order
    if req.enabled is not None:
        sq.enabled = req.enabled

    await db.flush()
    return {"code": "0", "message": "success", "data": None}


@router.delete("/{question_id}")
async def delete_question(
    question_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Soft-delete a sample question."""
    result = await db.execute(
        select(SampleQuestion).where(
            SampleQuestion.id == question_id,
            SampleQuestion.tenant_id == (user.tenant_id or "default"),
            SampleQuestion.deleted == 0,
        )
    )
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(status_code=404, detail="示例问题不存在")

    sq.deleted = 1
    await db.flush()
    return {"code": "0", "message": "success", "data": None}
