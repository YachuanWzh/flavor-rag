"""RAG trace logging — async trace runs and trace nodes."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import RagTraceRun, RagTraceNode, gen_id


class TraceLogger:
    """Logs full RAG pipeline traces to PostgreSQL for observability."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._trace_run: RagTraceRun | None = None

    async def trace_query(
        self,
        query: str,
        user_id: str,
        *,
        conversation_id: str = "",
        message_id: str | None = None,
        rewrite_query: str | None = None,
        intent: str | None = None,
    ) -> str:
        """Create a trace run and return its ID."""
        run = RagTraceRun(
            id=gen_id(),
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
            query=query,
            rewrite_query=rewrite_query,
            intent=intent,
            create_time=datetime.utcnow(),
        )
        self.db.add(run)
        await self.db.flush()
        self._trace_run = run
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
    ) -> str:
        """Log a pipeline node."""
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
            create_time=datetime.utcnow(),
        )
        self.db.add(node)
        await self.db.flush()
        return node.id

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
    ):
        """Update the trace run with final metrics."""
        from sqlalchemy import select

        if not trace_run_id:
            return

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

    async def get_trace(self, trace_run_id: str) -> dict | None:
        """Retrieve full trace with nodes."""
        from sqlalchemy import select

        result = await self.db.execute(
            select(RagTraceRun).where(RagTraceRun.id == trace_run_id)
        )
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
