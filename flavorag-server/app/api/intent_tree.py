"""Intent tree management API — CRUD for IntentNode."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, IntentNode, gen_id

router = APIRouter(prefix="/api/admin/intent-tree", tags=["intent-tree"])


class IntentNodeCreate(BaseModel):
    intent_code: str = Field(..., description="意图编码，如 code_search")
    name: str = Field(..., description="意图名称，如 代码搜索")
    level: int = Field(default=1, ge=1, le=5)
    parent_intent_code: str | None = None
    kb_id: str | None = None
    description: str | None = None
    collection_name: str | None = None
    search_channels: list[str] | None = None
    prompt_template: str | None = None
    sort_order: int = 0
    enabled: int = 1


class IntentNodeUpdate(BaseModel):
    name: str | None = None
    level: int | None = None
    parent_intent_code: str | None = None
    description: str | None = None
    collection_name: str | None = None
    search_channels: list[str] | None = None
    prompt_template: str | None = None
    sort_order: int | None = None
    enabled: int | None = None


# ---- CRUD ----


@router.get("")
async def list_intents(
    kb_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all intent nodes, optionally filtered by kb_id."""
    q = select(IntentNode).where(IntentNode.deleted == 0)
    if kb_id:
        q = q.where(IntentNode.kb_id == kb_id)
    q = q.order_by(IntentNode.level, IntentNode.sort_order)

    result = await db.execute(q)
    nodes = result.scalars().all()

    return {"code": "0", "message": "success", "data": [
        {
            "id": n.id,
            "intentCode": n.intent_code,
            "name": n.name,
            "level": n.level,
            "parentIntentCode": n.parent_intent_code,
            "kbId": n.kb_id,
            "description": n.description,
            "collectionName": n.collection_name,
            "searchChannels": n.search_channels,
            "promptTemplate": n.prompt_template,
            "sortOrder": n.sort_order,
            "enabled": n.enabled,
            "createTime": str(n.create_time),
        }
        for n in nodes
    ]}


@router.post("")
async def create_intent(
    req: IntentNodeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new intent node."""
    # Check for duplicate code
    existing = await db.execute(
        select(IntentNode).where(
            IntentNode.intent_code == req.intent_code,
            IntentNode.deleted == 0,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"意图编码 '{req.intent_code}' 已存在")

    node = IntentNode(
        id=gen_id(),
        intent_code=req.intent_code,
        name=req.name,
        level=req.level,
        parent_intent_code=req.parent_intent_code,
        kb_id=req.kb_id,
        description=req.description,
        collection_name=req.collection_name,
        search_channels=req.search_channels,
        prompt_template=req.prompt_template,
        sort_order=req.sort_order,
        enabled=req.enabled,
    )
    db.add(node)
    await db.flush()
    return {"code": "0", "message": "success", "data": {"id": node.id}}


@router.put("/{node_id}")
async def update_intent(
    node_id: str,
    req: IntentNodeUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update an existing intent node."""
    result = await db.execute(
        select(IntentNode).where(IntentNode.id == node_id, IntentNode.deleted == 0)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="意图节点不存在")

    if req.name is not None:
        node.name = req.name
    if req.level is not None:
        node.level = req.level
    if req.parent_intent_code is not None:
        node.parent_intent_code = req.parent_intent_code
    if req.description is not None:
        node.description = req.description
    if req.collection_name is not None:
        node.collection_name = req.collection_name
    if req.search_channels is not None:
        node.search_channels = req.search_channels
    if req.prompt_template is not None:
        node.prompt_template = req.prompt_template
    if req.sort_order is not None:
        node.sort_order = req.sort_order
    if req.enabled is not None:
        node.enabled = req.enabled

    await db.flush()
    return {"code": "0", "message": "success", "data": None}


@router.delete("/{node_id}")
async def delete_intent(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Soft-delete an intent node."""
    result = await db.execute(
        select(IntentNode).where(IntentNode.id == node_id, IntentNode.deleted == 0)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="意图节点不存在")

    node.deleted = 1
    await db.flush()
    return {"code": "0", "message": "success", "data": None}
