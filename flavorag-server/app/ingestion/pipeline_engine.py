"""Tenant-scoped, observable and failure-safe ingestion DAG execution."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.ingestion.nodes.base import IngestionContext, NodeResult, PipelineResult
from app.ingestion.nodes.chunker_node import ChunkerNode
from app.ingestion.nodes.enhancer import EnhancerNode
from app.ingestion.nodes.enricher import EnricherNode
from app.ingestion.nodes.fetcher import FetcherNode
from app.ingestion.nodes.indexer import IndexerNode
from app.ingestion.nodes.parser_node import ParserNode
from app.models import (
    IngestionPipeline as PipelineModel,
    IngestionPipelineNode,
    IngestionTask,
    IngestionTaskNode,
    gen_id,
)

_log = get_logger("flavorag.ingestion.engine")

_NODE_HANDLERS: dict[str, Any] = {
    "fetcher": FetcherNode(),
    "parser": ParserNode(),
    "chunker": ChunkerNode(),
    "enricher": EnricherNode(),
    "enhancer": EnhancerNode(),
    "indexer": IndexerNode(),
}
_DEFAULT_RETRIES = {
    "fetcher": 2,
    "parser": 1,
    "chunker": 0,
    "enricher": 2,
    "enhancer": 2,
    "indexer": 0,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validate_pipeline_graph(nodes: Iterable[Any]) -> None:
    node_list = list(nodes)
    if not node_list:
        raise ValueError("流水线至少需要一个节点")
    ids = [node.node_id for node in node_list]
    if len(ids) != len(set(ids)):
        raise ValueError("流水线存在重复节点 ID")
    known = set(ids)
    for node in node_list:
        if node.node_type not in _NODE_HANDLERS:
            raise ValueError(f"不支持的节点类型: {node.node_type}")
        if node.next_node_id and node.next_node_id not in known:
            raise ValueError(f"节点 {node.node_id} 指向不存在的节点")

    next_by_id = {
        node.node_id: node.next_node_id
        for node in node_list
        if node.next_node_id
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("流水线存在循环依赖")
        if node_id in visited:
            return
        visiting.add(node_id)
        next_id = next_by_id.get(node_id)
        if next_id:
            visit(next_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in ids:
        visit(node_id)


class IngestionEngine:
    async def execute_pipeline(
        self,
        pipeline_id: str,
        source_type: str,
        source_location: str,
        source_file_name: str = "",
        kb_id: str = "",
        doc_id: str = "",
        user_id: str = "",
        tenant_id: str = "default",
        idempotency_key: str | None = None,
        parent_task_id: str | None = None,
        attempt: int = 1,
        generation: str = "v1",
        sla_ms: int = 300_000,
        db: AsyncSession | None = None,
    ) -> PipelineResult:
        task = IngestionTask(
            id=gen_id(),
            tenant_id=tenant_id,
            pipeline_id=pipeline_id,
            kb_id=kb_id or None,
            doc_id=doc_id or None,
            trace_id=uuid.uuid4().hex,
            idempotency_key=idempotency_key,
            parent_task_id=parent_task_id,
            attempt=max(1, attempt),
            source_type=source_type,
            source_location=source_location,
            source_file_name=source_file_name,
            status="running",
            sla_ms=max(1_000, sla_ms),
            heartbeat_at=_utcnow(),
            started_at=_utcnow(),
            created_by=user_id,
            logs_json={"generation": generation},
        )

        if db is not None:
            db.add(task)
            await db.flush()
            return await self._execute_and_capture(db, task)

        async with async_session_factory() as session:
            session.add(task)
            await session.flush()
            result = await self._execute_and_capture(session, task)
            await session.commit()
            return result

    async def _execute_and_capture(
        self,
        session: AsyncSession,
        task: IngestionTask,
    ) -> PipelineResult:
        started = time.monotonic()
        try:
            return await self._run(session, task)
        except Exception as exc:
            total_ms = int((time.monotonic() - started) * 1000)
            task.status = "error"
            task.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            task.total_duration_ms = total_ms
            task.completed_at = _utcnow()
            task.heartbeat_at = _utcnow()
            task.logs_json = {
                **(task.logs_json or {}),
                "failure_type": type(exc).__name__,
            }
            await session.flush()
            _log.error(
                "pipeline_execution_failed",
                pipeline_id=task.pipeline_id,
                task_id=task.id,
                trace_id=task.trace_id,
                error=str(exc),
            )
            return PipelineResult(
                task_id=task.id,
                status="error",
                error_message=task.error_message,
                total_duration_ms=total_ms,
            )

    async def _run(
        self,
        session: AsyncSession,
        task: IngestionTask,
    ) -> PipelineResult:
        started = time.monotonic()
        pipeline = (
            await session.execute(
                select(PipelineModel).where(
                    PipelineModel.id == task.pipeline_id,
                    PipelineModel.tenant_id == task.tenant_id,
                    PipelineModel.enabled == 1,
                    PipelineModel.deleted == 0,
                )
            )
        ).scalar_one_or_none()
        if pipeline is None:
            raise ValueError(f"流水线不存在或已停用: {task.pipeline_id}")

        node_defs = list(
            (
                await session.execute(
                    select(IngestionPipelineNode)
                    .where(
                        IngestionPipelineNode.pipeline_id == task.pipeline_id,
                        IngestionPipelineNode.deleted == 0,
                    )
                    .order_by(IngestionPipelineNode.create_time)
                )
            ).scalars().all()
        )
        validate_pipeline_graph(node_defs)
        node_map = {node.node_id: node for node in node_defs}
        incoming = {node.node_id: 0 for node in node_defs}
        for node in node_defs:
            if node.next_node_id:
                incoming[node.next_node_id] += 1
        queue = [node_id for node_id, count in incoming.items() if count == 0]

        from app.models import KnowledgeBase

        kb_config = (
            await session.execute(
                select(
                    KnowledgeBase.embedding_model,
                    KnowledgeBase.active_collection_name,
                ).where(KnowledgeBase.id == (task.kb_id or ""))
            )
        ).first()
        context = IngestionContext(
            source_type=task.source_type,
            source_location=task.source_location,
            source_file_name=task.source_file_name or "",
            kb_id=task.kb_id or "",
            doc_id=task.doc_id or "",
            db=session,
            generation=(task.logs_json or {}).get("generation", "v1"),
            settings={
                "embedding_model": kb_config[0] if kb_config else "",
                "collection_name": kb_config[1] if kb_config else "",
            },
            metadata={
                "task_id": task.id,
                "trace_id": task.trace_id,
                "tenant_id": task.tenant_id,
            },
        )
        visited: set[str] = set()
        node_results: list[NodeResult] = []
        final_status = "success"
        final_error = ""

        while queue:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            node_def = node_map[node_id]
            context.settings = {
                **context.settings,
                **(node_def.settings_json or {}),
            }
            result, node_attempt, node_started, node_completed = await self._run_node(
                node_def,
                context,
            )
            result.node_id = node_id
            result.node_type = node_def.node_type
            node_results.append(result)
            session.add(
                IngestionTaskNode(
                    id=gen_id(),
                    task_id=task.id,
                    pipeline_id=task.pipeline_id,
                    node_id=node_id,
                    node_type=node_def.node_type,
                    node_order=len(visited),
                    attempt=node_attempt,
                    status=result.status,
                    duration_ms=result.duration_ms,
                    message=result.message,
                    error_message=result.error_message,
                    output_json={
                        **(result.output or {}),
                        "attempts": node_attempt,
                    },
                    started_at=node_started,
                    completed_at=node_completed,
                )
            )
            task.heartbeat_at = _utcnow()
            await session.flush()

            if result.status == "error":
                final_status = "error"
                final_error = result.error_message
                if context.settings.get("stop_on_error", True):
                    break
            if node_def.next_node_id and node_def.next_node_id not in visited:
                queue.append(node_def.next_node_id)

        if final_status == "success" and len(visited) != len(node_defs):
            final_status = "error"
            final_error = "流水线存在不可达节点，执行未完整结束"

        total_ms = int((time.monotonic() - started) * 1000)
        task.status = final_status
        task.completed_at = _utcnow()
        task.heartbeat_at = _utcnow()
        task.total_duration_ms = total_ms
        task.error_message = final_error or None
        task.chunk_count = len(context.chunks)
        task.logs_json = {
            "node_results": [
                {
                    "node_id": result.node_id,
                    "type": result.node_type,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                }
                for result in node_results
            ],
            "sla_breached": total_ms > (task.sla_ms or 300_000),
            "trace_id": task.trace_id,
        }
        await session.flush()
        _log.info(
            "pipeline_complete",
            pipeline_id=task.pipeline_id,
            task_id=task.id,
            trace_id=task.trace_id,
            tenant_id=task.tenant_id,
            status=final_status,
            chunk_count=len(context.chunks),
            total_ms=total_ms,
        )
        return PipelineResult(
            task_id=task.id,
            status=final_status,
            error_message=final_error,
            chunk_count=len(context.chunks),
            total_duration_ms=total_ms,
            node_results=node_results,
        )

    async def _run_node(
        self,
        node_def: IngestionPipelineNode,
        context: IngestionContext,
    ) -> tuple[NodeResult, int, datetime, datetime]:
        handler = _NODE_HANDLERS[node_def.node_type]
        settings = node_def.settings_json or {}
        max_retries = min(
            5,
            max(
                0,
                int(settings.get("max_retries", _DEFAULT_RETRIES[node_def.node_type])),
            ),
        )
        timeout_seconds = min(
            600.0,
            max(1.0, float(settings.get("timeout_ms", 120_000)) / 1000),
        )
        backoff_ms = min(10_000, max(0, int(settings.get("retry_backoff_ms", 250))))
        node_started = _utcnow()
        monotonic_started = time.monotonic()
        last_error = ""

        for attempt in range(1, max_retries + 2):
            try:
                result = await asyncio.wait_for(
                    handler(context),
                    timeout=timeout_seconds,
                )
                result.duration_ms = int(
                    (time.monotonic() - monotonic_started) * 1000
                )
                if result.status != "error":
                    return result, attempt, node_started, _utcnow()
                last_error = result.error_message or result.message or "节点执行失败"
            except (TimeoutError, asyncio.TimeoutError):
                last_error = f"节点超时（{int(timeout_seconds * 1000)}ms）"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt <= max_retries:
                _log.warning(
                    "pipeline_node_retry",
                    node_id=node_def.node_id,
                    node_type=node_def.node_type,
                    attempt=attempt,
                    error=last_error,
                )
                if backoff_ms:
                    await asyncio.sleep(backoff_ms * (2 ** (attempt - 1)) / 1000)

        return (
            NodeResult(
                status="error",
                error_message=last_error[:2000],
                duration_ms=int((time.monotonic() - monotonic_started) * 1000),
            ),
            max_retries + 1,
            node_started,
            _utcnow(),
        )
