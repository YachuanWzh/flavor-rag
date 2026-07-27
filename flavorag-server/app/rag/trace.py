"""RAG trace logging — async trace runs and trace nodes."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import RagTraceRun, RagTraceNode, gen_id
from app.observability import otel
from app.observability.metrics import RAG_RUNS


class TraceLogger:
    """Logs full RAG pipeline traces to PostgreSQL for observability.

    NOTE on SQLite concurrency: every write below commits immediately.
    SQLite allows only a single writer at a time; the RAG pipeline can run
    for tens of seconds, so if trace writes kept the request transaction
    open they would hold the write lock for the whole run and concurrent
    requests would fail with "database is locked".  Trace data is
    observability-only, so committing it eagerly (independently of the
    business transaction) is safe and keeps the write-lock window tiny.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self._trace_run: RagTraceRun | None = None
        self._otel_span = None

    async def _commit(self) -> None:
        """Commit trace writes immediately to release the SQLite write lock."""
        try:
            await self.db.commit()
        except Exception:  # pragma: no cover - best effort observability
            await self.db.rollback()

    async def trace_query(
        self,
        query: str,
        user_id: str,
        *,
        conversation_id: str = "",
        message_id: str | None = None,
        tenant_id: str = "default",
        kb_id: str | None = None,
        rewrite_query: str | None = None,
        intent: str | None = None,
    ) -> str:
        """Create a trace run and return its ID."""
        run = RagTraceRun(
            id=gen_id(),
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
            tenant_id=tenant_id,
            kb_id=kb_id,
            query=query,
            rewrite_query=rewrite_query,
            intent=intent,
            create_time=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.db.add(run)
        await self.db.flush()
        await self._commit()
        self._trace_run = run
        self._otel_span = otel.start_rag_span(
            "rag.run",
            {
                "rag.trace_id": run.id,
                "rag.tenant_id": tenant_id,
                "rag.kb_id": kb_id or "",
                "rag.conversation_id": conversation_id,
            },
        )
        return run.id

    async def trace_node(
        self,
        trace_run_id: str,
        node_type: str,
        node_name: str,
        start_time: datetime,
        end_time: datetime,
        *,
        input_data: dict | None = None,
        output_data: dict | None = None,
        status: str = "success",
        error_message: str | None = None,
        rejection_reason: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Log a pipeline node."""
        if not trace_run_id:
            return ""
        node = RagTraceNode(
            id=gen_id(),
            trace_run_id=trace_run_id,
            node_type=node_type,
            node_name=node_name,
            start_time=start_time,
            end_time=end_time,
            duration_ms=int((end_time - start_time).total_seconds() * 1000) if start_time and end_time else 0,
            input_data=input_data,
            output_data=output_data,
            status=status,
            error_message=error_message,
            create_time=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.db.add(node)
        await self.db.flush()
        await self._commit()
        if start_time and end_time:
            otel.record_child_span(
                self._otel_span,
                f"rag.{node_type}.{node_name}",
                start_time,
                end_time,
                attributes={"rag.node_status": status},
                error=error_message if status != "success" else None,
            )
        return node.id

    async def update_understanding(
        self,
        trace_run_id: str,
        *,
        rewrite_query: str | None,
        intent: str | None,
        metadata: dict | None = None,
    ) -> None:
        """Persist structured query-understanding output on the parent trace."""
        from sqlalchemy import select

        if not trace_run_id:
            return
        result = await self.db.execute(
            select(RagTraceRun).where(RagTraceRun.id == trace_run_id)
        )
        run = result.scalar_one_or_none()
        if not run:
            return
        run.rewrite_query = rewrite_query
        run.intent = intent
        if metadata:
            run.metadata_json = {**(run.metadata_json or {}), **metadata}
        await self.db.flush()
        await self._commit()

    async def finalize(
        self,
        trace_run_id: str,
        *,
        search_duration_ms: int = 0,
        llm_duration_ms: int = 0,
        total_duration_ms: int = 0,
        recall_count: int = 0,
        final_count: int = 0,
        model_name: str = "",
        status: str = "success",
        error_message: str | None = None,
        rejection_reason: str | None = None,
        metadata: dict | None = None,
    ):
        """Update the trace run with final metrics."""
        from sqlalchemy import select

        if not trace_run_id:
            return

        RAG_RUNS.labels(status=status).inc()
        otel.end_rag_span(
            self._otel_span,
            attributes={
                "rag.status": status,
                "rag.recall_count": recall_count,
                "rag.final_count": final_count,
                "rag.model": model_name,
                "rag.rejection_reason": rejection_reason,
            },
            error=error_message if status != "success" else None,
        )
        self._otel_span = None

        result = await self.db.execute(
            select(RagTraceRun).where(RagTraceRun.id == trace_run_id)
        )
        run = result.scalar_one_or_none()
        if run:
            run.search_duration_ms = search_duration_ms
            run.llm_duration_ms = llm_duration_ms
            run.total_duration_ms = total_duration_ms
            run.recall_count = recall_count
            run.final_count = final_count
            run.model_name = model_name
            run.status = status
            run.error_message = error_message
            run.rejection_reason = rejection_reason
            run.metadata_json = metadata
            await self.db.flush()
            await self._commit()

    async def get_trace(
        self,
        trace_run_id: str,
        *,
        tenant_id: str | None = None,
    ) -> dict | None:
        """Retrieve full trace with nodes."""
        from sqlalchemy import select

        predicates = [RagTraceRun.id == trace_run_id]
        if tenant_id is not None:
            predicates.append(RagTraceRun.tenant_id == tenant_id)
        result = await self.db.execute(select(RagTraceRun).where(*predicates))
        run = result.scalar_one_or_none()
        if not run:
            return None

        nodes_result = await self.db.execute(
            select(RagTraceNode).where(RagTraceNode.trace_run_id == trace_run_id)
        )
        nodes = nodes_result.scalars().all()

        return {
            "run": {
                "id": run.id,
                "query": run.query,
                "rewrite_query": run.rewrite_query,
                "intent": run.intent,
                "search_duration_ms": run.search_duration_ms,
                "llm_duration_ms": run.llm_duration_ms,
                "total_duration_ms": run.total_duration_ms,
                "recall_count": run.recall_count,
                "final_count": run.final_count,
                "model_name": run.model_name,
                "status": run.status,
                "rejection_reason": run.rejection_reason,
                "metadata": run.metadata_json,
                "create_time": str(run.create_time),
            },
            "nodes": [
                {
                    "id": n.id,
                    "node_type": n.node_type,
                    "node_name": n.node_name,
                    "duration_ms": n.duration_ms,
                    "status": n.status,
                    "input_data": n.input_data,
                    "output_data": n.output_data,
                }
                for n in nodes
            ],
        }
