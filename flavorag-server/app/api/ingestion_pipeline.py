"""Ingestion pipeline management API — CRUD + execution."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import (
    User,
    IngestionPipeline as PipelineModel,
    IngestionPipelineNode as PipelineNodeModel,
    IngestionTask,
    IngestionTaskNode,
    gen_id,
)
from app.ingestion.pipeline_engine import IngestionEngine

router = APIRouter(prefix="/api/admin/ingestion", tags=["admin-ingestion"])


# ---- Request / Response models ----

class PipelineCreateRequest(BaseModel):
    name: str = Field(..., description="流水线名称")
    description: str | None = Field(None, description="流水线描述")
    nodes: list["NodeDef"] = Field(default_factory=list, description="节点列表")


class NodeDef(BaseModel):
    node_id: str = Field(..., description="节点标识")
    node_type: str = Field(..., description="节点类型: fetcher/parser/chunker/enricher/enhancer/indexer")
    next_node_id: str | None = Field(None, description="下一个节点ID")
    settings_json: dict | None = Field(None, description="节点配置JSON")
    condition_json: dict | None = Field(None, description="条件JSON")


class PipelineUpdateRequest(BaseModel):
    name: str | None = Field(None)
    description: str | None = Field(None)
    nodes: list[NodeDef] | None = Field(None)


class TaskExecuteRequest(BaseModel):
    pipeline_id: str = Field(..., description="流水线ID")
    source_type: str = Field(..., description="来源类型: file/url")
    source_location: str = Field(..., description="来源地址")
    source_file_name: str = Field("", description="原始文件名")
    kb_id: str = Field("", description="知识库ID")
    doc_id: str = Field("", description="文档ID")


# ---- Pipeline CRUD ----

@router.get("/pipelines")
async def list_pipelines(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(PipelineModel).where(PipelineModel.deleted == 0)
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0
    offset = (page - 1) * page_size
    stmt = stmt.order_by(desc(PipelineModel.create_time)).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {
        "code": "0", "message": "success",
        "data": {
            "total": total, "page": page, "pageSize": page_size,
            "rows": [
                {
                    "id": r.id, "name": r.name, "description": r.description,
                    "createdBy": r.created_by, "createTime": str(r.create_time),
                }
                for r in rows
            ],
        },
    }


@router.post("/pipelines")
async def create_pipeline(
    req: PipelineCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pipeline = PipelineModel(
        id=gen_id(),
        name=req.name,
        description=req.description or "",
        created_by=user.id,
    )
    db.add(pipeline)

    for i, nd in enumerate(req.nodes):
        node = PipelineNodeModel(
            id=gen_id(),
            pipeline_id=pipeline.id,
            node_id=nd.node_id,
            node_type=nd.node_type,
            next_node_id=nd.next_node_id,
            settings_json=nd.settings_json,
            condition_json=nd.condition_json,
            created_by=user.id,
        )
        db.add(node)

    await db.flush()
    return {"code": "0", "message": "success", "data": {"id": pipeline.id}}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(
    pipeline_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PipelineModel).where(PipelineModel.id == pipeline_id, PipelineModel.deleted == 0)
    )
    pipeline = result.scalar_one_or_none()
    if not pipeline:
        raise HTTPException(status_code=404, detail="流水线不存在")

    nodes_result = await db.execute(
        select(PipelineNodeModel).where(
            PipelineNodeModel.pipeline_id == pipeline_id,
            PipelineNodeModel.deleted == 0,
        ).order_by(PipelineNodeModel.create_time)
    )
    nodes = nodes_result.scalars().all()

    return {
        "code": "0", "message": "success",
        "data": {
            "id": pipeline.id,
            "name": pipeline.name,
            "description": pipeline.description,
            "createdBy": pipeline.created_by,
            "createTime": str(pipeline.create_time),
            "nodes": [
                {
                    "id": n.id, "nodeId": n.node_id, "nodeType": n.node_type,
                    "nextNodeId": n.next_node_id, "settingsJson": n.settings_json,
                    "conditionJson": n.condition_json,
                }
                for n in nodes
            ],
        },
    }


@router.put("/pipelines/{pipeline_id}")
async def update_pipeline(
    pipeline_id: str,
    req: PipelineUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PipelineModel).where(PipelineModel.id == pipeline_id, PipelineModel.deleted == 0)
    )
    pipeline = result.scalar_one_or_none()
    if not pipeline:
        raise HTTPException(status_code=404, detail="流水线不存在")

    if req.name is not None:
        pipeline.name = req.name
    if req.description is not None:
        pipeline.description = req.description
    if req.nodes is not None:
        # Soft-delete old nodes
        old_nodes = await db.execute(
            select(PipelineNodeModel).where(
                PipelineNodeModel.pipeline_id == pipeline_id,
                PipelineNodeModel.deleted == 0,
            )
        )
        for n in old_nodes.scalars().all():
            n.deleted = 1
        # Create new nodes
        for nd in req.nodes:
            node = PipelineNodeModel(
                id=gen_id(),
                pipeline_id=pipeline_id,
                node_id=nd.node_id,
                node_type=nd.node_type,
                next_node_id=nd.next_node_id,
                settings_json=nd.settings_json,
                condition_json=nd.condition_json,
                created_by=user.id,
            )
            db.add(node)

    pipeline.updated_by = user.id
    await db.flush()
    return {"code": "0", "message": "success", "data": None}


@router.delete("/pipelines/{pipeline_id}")
async def delete_pipeline(
    pipeline_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PipelineModel).where(PipelineModel.id == pipeline_id, PipelineModel.deleted == 0)
    )
    pipeline = result.scalar_one_or_none()
    if not pipeline:
        raise HTTPException(status_code=404, detail="流水线不存在")
    pipeline.deleted = 1
    await db.flush()
    return {"code": "0", "message": "success", "data": None}


# ---- Task Execution ----

@router.post("/tasks/execute")
async def execute_task(
    req: TaskExecuteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    engine = IngestionEngine()
    result = await engine.execute_pipeline(
        pipeline_id=req.pipeline_id,
        source_type=req.source_type,
        source_location=req.source_location,
        source_file_name=req.source_file_name,
        kb_id=req.kb_id,
        doc_id=req.doc_id,
        user_id=user.id,
        db=db,
    )
    return {
        "code": "0", "message": "success",
        "data": {
            "taskId": result.task_id,
            "status": result.status,
            "errorMessage": result.error_message,
            "chunkCount": result.chunk_count,
            "totalDurationMs": result.total_duration_ms,
            "nodes": [
                {
                    "nodeId": r.node_id, "nodeType": r.node_type,
                    "status": r.status, "durationMs": r.duration_ms,
                    "message": r.message, "errorMessage": r.error_message,
                }
                for r in result.node_results
            ],
        },
    }


@router.get("/tasks")
async def list_tasks(
    pipeline_id: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(IngestionTask).where(IngestionTask.deleted == 0)
    if pipeline_id:
        stmt = stmt.where(IngestionTask.pipeline_id == pipeline_id)
    if status:
        stmt = stmt.where(IngestionTask.status == status)

    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    stmt = stmt.order_by(desc(IngestionTask.create_time)).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return {
        "code": "0", "message": "success",
        "data": {
            "total": total, "page": page, "pageSize": page_size,
            "rows": [
                {
                    "id": r.id, "pipelineId": r.pipeline_id,
                    "sourceType": r.source_type, "sourceLocation": r.source_location[:200],
                    "sourceFileName": r.source_file_name, "status": r.status,
                    "chunkCount": r.chunk_count, "errorMessage": r.error_message,
                    "startedAt": str(r.started_at) if r.started_at else None,
                    "completedAt": str(r.completed_at) if r.completed_at else None,
                    "createTime": str(r.create_time),
                }
                for r in rows
            ],
        },
    }


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(IngestionTask).where(IngestionTask.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    nodes_result = await db.execute(
        select(IngestionTaskNode).where(
            IngestionTaskNode.task_id == task_id,
            IngestionTaskNode.deleted == 0,
        ).order_by(IngestionTaskNode.node_order)
    )
    nodes = nodes_result.scalars().all()

    return {
        "code": "0", "message": "success",
        "data": {
            "id": task.id,
            "pipelineId": task.pipeline_id,
            "sourceType": task.source_type,
            "sourceLocation": task.source_location,
            "sourceFileName": task.source_file_name,
            "status": task.status,
            "chunkCount": task.chunk_count,
            "errorMessage": task.error_message,
            "startedAt": str(task.started_at) if task.started_at else None,
            "completedAt": str(task.completed_at) if task.completed_at else None,
            "nodes": [
                {
                    "nodeId": n.node_id, "nodeType": n.node_type,
                    "nodeOrder": n.node_order, "status": n.status,
                    "durationMs": n.duration_ms, "message": n.message,
                    "errorMessage": n.error_message,
                }
                for n in nodes
            ],
        },
    }
