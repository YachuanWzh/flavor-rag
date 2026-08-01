"""Query term mapping API — CRUD for QueryTermMapping (synonym maps)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, QueryTermMapping, gen_id
from app.time_utils import utc_isoformat

router = APIRouter(prefix="/api/admin/query-term-mapping", tags=["query-term-mapping"])


class TermMappingCreate(BaseModel):
    source_term: str = Field(..., max_length=128, description="源词/同义词")
    target_term: str = Field(..., max_length=128, description="目标词/标准词")
    kb_id: str | None = None
    mapping_type: str = Field(default="EXACT", description="映射类型: EXACT / SYNONYM / ABBREVIATION")
    enabled: int = 1


class TermMappingUpdate(BaseModel):
    source_term: str | None = None
    target_term: str | None = None
    mapping_type: str | None = None
    enabled: int | None = None


@router.get("")
async def list_mappings(
    kb_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all query term mappings, optionally filtered by kb_id."""
    q = select(QueryTermMapping).where(
        QueryTermMapping.deleted == 0,
        QueryTermMapping.tenant_id == (user.tenant_id or "default"),
    )
    if kb_id:
        q = q.where(QueryTermMapping.kb_id == kb_id)
    q = q.order_by(QueryTermMapping.mapping_type, QueryTermMapping.source_term)

    result = await db.execute(q)
    items = result.scalars().all()

    return {"code": "0", "message": "success", "data": [
        {
            "id": m.id,
            "sourceTerm": m.source_term,
            "targetTerm": m.target_term,
            "kbId": m.kb_id,
            "mappingType": m.mapping_type,
            "enabled": m.enabled,
            "hitCount": m.hit_count or 0,
            "createTime": utc_isoformat(m.create_time),
        }
        for m in items
    ]}


@router.post("")
async def create_mapping(
    req: TermMappingCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new query term mapping."""
    # Check for duplicate source + target pair
    existing = await db.execute(
        select(QueryTermMapping).where(
            QueryTermMapping.source_term == req.source_term,
            QueryTermMapping.target_term == req.target_term,
            QueryTermMapping.kb_id == req.kb_id,
            QueryTermMapping.tenant_id == (user.tenant_id or "default"),
            QueryTermMapping.deleted == 0,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该映射关系已存在")

    mapping = QueryTermMapping(
        id=gen_id(),
        tenant_id=user.tenant_id or "default",
        source_term=req.source_term,
        target_term=req.target_term,
        kb_id=req.kb_id,
        mapping_type=req.mapping_type,
        enabled=req.enabled,
    )
    db.add(mapping)
    await db.flush()
    return {"code": "0", "message": "success", "data": {"id": mapping.id}}


@router.put("/{mapping_id}")
async def update_mapping(
    mapping_id: str,
    req: TermMappingUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a query term mapping."""
    result = await db.execute(
        select(QueryTermMapping).where(
            QueryTermMapping.id == mapping_id,
            QueryTermMapping.tenant_id == (user.tenant_id or "default"),
            QueryTermMapping.deleted == 0,
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="映射不存在")

    if req.source_term is not None:
        mapping.source_term = req.source_term
    if req.target_term is not None:
        mapping.target_term = req.target_term
    if req.mapping_type is not None:
        mapping.mapping_type = req.mapping_type
    if req.enabled is not None:
        mapping.enabled = req.enabled

    await db.flush()
    return {"code": "0", "message": "success", "data": None}


@router.delete("/{mapping_id}")
async def delete_mapping(
    mapping_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Soft-delete a query term mapping."""
    result = await db.execute(
        select(QueryTermMapping).where(
            QueryTermMapping.id == mapping_id,
            QueryTermMapping.tenant_id == (user.tenant_id or "default"),
            QueryTermMapping.deleted == 0,
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="映射不存在")

    mapping.deleted = 1
    await db.flush()
    return {"code": "0", "message": "success", "data": None}
