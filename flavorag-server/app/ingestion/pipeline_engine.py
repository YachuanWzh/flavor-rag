"""Ingestion pipeline engine — DAG execution of pipeline nodes.

Orchestrates nodes (Fetcher→Parser→Chunker→Enricher→Enhancer→Indexer)
with support for:
  - Sequential and conditional execution
  - Per-node configuration from pipeline definitions
  - Progress tracking and error handling
  - Chunk processing time logging to KnowledgeDocumentChunkLog
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.models import (
    IngestionPipeline as PipelineModel,
    IngestionPipelineNode,
    IngestionTask,
    IngestionTaskNode,
    KnowledgeDocumentChunkLog,
    gen_id,
)
from app.ingestion.nodes.base import (
    IngestionContext,
    NodeResult,
    PipelineResult,
)
from app.ingestion.nodes.fetcher import FetcherNode
from app.ingestion.nodes.parser_node import ParserNode
from app.ingestion.nodes.chunker_node import ChunkerNode
from app.ingestion.nodes.enricher import EnricherNode
from app.ingestion.nodes.enhancer import EnhancerNode
from app.ingestion.nodes.indexer import IndexerNode

_log = get_logger("flavorag.ingestion.engine")

# Node type → handler mapping
_NODE_HANDLERS: dict[str, Any] = {
    "fetcher": FetcherNode(),
    "parser": ParserNode(),
    "chunker": ChunkerNode(),
    "enricher": EnricherNode(),
    "enhancer": EnhancerNode(),
    "indexer": IndexerNode(),
}


class IngestionEngine:
    """Execute ingestion pipelines as DAGs of nodes.

    Supports two modes:
      1. Pipeline-driven: Load pipeline definition from DB, execute its nodes.
      2. Direct: Execute a predefined sequence (used by existing IngestionPipeline).
    """

    async def execute_pipeline(
        self,
        pipeline_id: str,
        source_type: str,
        source_location: str,
        source_file_name: str = "",
        kb_id: str = "",
        doc_id: str = "",
        user_id: str = "",
        db: AsyncSession | None = None,
    ) -> PipelineResult:
        """Execute a saved pipeline definition by ID."""
        t0 = time.time()

        # Create task record
        task = IngestionTask(
            id=gen_id(),
            pipeline_id=pipeline_id,
            source_type=source_type,
            source_location=source_location,
            source_file_name=source_file_name,
            status="running",
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            created_by=user_id,
        )

        own_session = db is None
        if own_session:
            from app.database.session import async_session_factory
            session = async_session_factory()
        else:
            session = db

        try:
            if own_session:
                async with session as s:
                    s.add(task)
                    await s.flush()
                    return await self._run(s, task, pipeline_id, source_type,
                                           source_location, source_file_name, kb_id, doc_id, user_id)
            else:
                session.add(task)
                await session.flush()
                return await self._run(session, task, pipeline_id, source_type,
                                       source_location, source_file_name, kb_id, doc_id, user_id)
        except Exception as exc:
            _log.error("pipeline_execution_failed", pipeline_id=pipeline_id, error=str(exc))
            total_ms = int((time.time() - t0) * 1000)
            return PipelineResult(
                task_id=task.id, status="error", error_message=str(exc), total_duration_ms=total_ms,
            )

    async def _run(
        self,
        session: AsyncSession,
        task: IngestionTask,
        pipeline_id: str,
        source_type: str,
        source_location: str,
        source_file_name: str,
        kb_id: str,
        doc_id: str,
        user_id: str,
    ) -> PipelineResult:
        t0 = time.time()

        # Load pipeline definition
        from sqlalchemy import select
        result = await session.execute(
            select(PipelineModel).where(PipelineModel.id == pipeline_id, PipelineModel.deleted == 0)
        )
        pipeline = result.scalar_one_or_none()
        if not pipeline:
            raise ValueError(f"Pipeline not found: {pipeline_id}")

        # Load nodes
        nodes_result = await session.execute(
            select(IngestionPipelineNode).where(
                IngestionPipelineNode.pipeline_id == pipeline_id,
                IngestionPipelineNode.deleted == 0,
            )
        )
        node_defs = nodes_result.scalars().all()

        if not node_defs:
            raise ValueError(f"No nodes in pipeline: {pipeline_id}")

        # Build DAG: find root nodes (no incoming edges)
        node_map: dict[str, IngestionPipelineNode] = {n.node_id: n for n in node_defs}
        incoming = {n.node_id: 0 for n in node_defs}
        for n in node_defs:
            if n.next_node_id and n.next_node_id in incoming:
                incoming[n.next_node_id] += 1

        root_nodes = [node_id for node_id, count in incoming.items() if count == 0]
        if not root_nodes:
            root_nodes = [node_defs[0].node_id]

        # Build context
        ctx = IngestionContext(
            source_type=source_type,
            source_location=source_location,
            source_file_name=source_file_name,
            kb_id=kb_id,
            doc_id=doc_id,
        )

        # Execute nodes in topological order
        visited: set[str] = set()
        queue: list[str] = list(root_nodes)
        node_results: list[NodeResult] = []
        task_nodes: list[IngestionTaskNode] = []
        final_status = "success"
        final_error = ""

        while queue:
            node_id = queue.pop(0)
            if node_id in visited or node_id not in node_map:
                continue
            visited.add(node_id)

            node_def = node_map[node_id]
            handler = _NODE_HANDLERS.get(node_def.node_type)
            if not handler:
                _log.warning("unknown_node_type", node_id=node_id, node_type=node_def.node_type)
                continue

            # Merge node settings
            if node_def.settings_json:
                ctx.settings = {**ctx.settings, **node_def.settings_json}

            # Execute node
            t_node = time.time()
            result = await handler(ctx)
            node_duration = int((time.time() - t_node) * 1000)
            result.duration_ms = node_duration
            result.node_id = node_id
            result.node_type = node_def.node_type
            node_results.append(result)

            # Record task node
            tn = IngestionTaskNode(
                id=gen_id(),
                task_id=task.id,
                pipeline_id=pipeline_id,
                node_id=node_id,
                node_type=node_def.node_type,
                node_order=len(visited),
                status=result.status,
                duration_ms=node_duration,
                message=result.message,
                error_message=result.error_message,
                output_json=result.output,
            )
            session.add(tn)
            task_nodes.append(tn)

            if result.status == "error":
                final_status = "error"
                final_error = result.error_message
                _log.error("node_failed", node_id=node_id, error=result.error_message)
                # Check condition: stop on error or continue?
                stop_on_error = ctx.settings.get("stop_on_error", True)
                if stop_on_error:
                    break

            # Enqueue next nodes
            if node_def.next_node_id and node_def.next_node_id not in visited:
                queue.append(node_def.next_node_id)

        # Update task
        total_ms = int((time.time() - t0) * 1000)
        task.status = final_status
        task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        task.error_message = final_error if final_error else None
        task.chunk_count = len(ctx.chunks)
        task.logs_json = {
            "node_results": [
                {"node_id": r.node_id, "type": r.node_type, "status": r.status, "duration_ms": r.duration_ms}
                for r in node_results
            ]
        }

        await session.flush()

        _log.info(
            "pipeline_complete",
            pipeline_id=pipeline_id,
            task_id=task.id,
            status=final_status,
            chunk_count=len(ctx.chunks),
            total_ms=total_ms,
        )

        return PipelineResult(
            task_id=task.id,
            status=final_status,
            error_message=final_error,
            chunk_count=len(ctx.chunks),
            total_duration_ms=total_ms,
            node_results=node_results,
        )

    async def log_chunk_processing(
        self,
        doc_id: str,
        status: str,
        *,
        process_mode: str = "pipeline",
        chunk_strategy: str = "",
        pipeline_id: str = "",
        extract_duration: int = 0,
        chunk_duration: int = 0,
        embed_duration: int = 0,
        persist_duration: int = 0,
        total_duration: int = 0,
        chunk_count: int = 0,
        error_message: str = "",
        db: AsyncSession | None = None,
    ) -> str:
        """Log chunk processing performance metrics."""
        log_id = gen_id()
        entry = KnowledgeDocumentChunkLog(
            id=log_id,
            doc_id=doc_id,
            status=status,
            process_mode=process_mode,
            chunk_strategy=chunk_strategy,
            pipeline_id=pipeline_id,
            extract_duration=extract_duration,
            chunk_duration=chunk_duration,
            embed_duration=embed_duration,
            persist_duration=persist_duration,
            total_duration=total_duration,
            chunk_count=chunk_count,
            error_message=error_message,
            start_time=datetime.now(timezone.utc).replace(tzinfo=None),
            end_time=datetime.now(timezone.utc).replace(tzinfo=None),
        )

        if db is not None:
            db.add(entry)
            await db.flush()
        else:
            async with async_session_factory() as session:
                session.add(entry)
                await session.commit()

        return log_id
