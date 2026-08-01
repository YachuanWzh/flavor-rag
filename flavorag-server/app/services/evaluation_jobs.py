"""Durable, multi-replica-safe evaluation job worker."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, or_, select, update

from app.config.logging_config import get_logger
from app.evaluation import DATASET_PATH
from app.evaluation.runner import (
    assess_quality_gates,
    calculate_case_metrics,
    calculate_metrics,
    load_dataset,
    run_evaluation,
)
from app.evaluation.cases import to_evaluation_case
from app.models import EvaluationDatasetCase, EvaluationRun, KnowledgeBase
from app.rag.pipeline import RAGContext, RAGPipeline, RetrievalScope

_log = get_logger("flavorag.evaluation.worker")
_DATASET = DATASET_PATH


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EvaluationJobWorker:
    """Claim persisted evaluation runs and resume them after process failure."""

    def __init__(self, poll_interval_sec: int = 5):
        self.poll_interval_sec = poll_interval_sec
        self.worker_id = f"{os.getpid()}-{id(self):x}"
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name="evaluation-job-worker"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        from app.database.session import async_session_factory

        while self._running:
            try:
                processed = await self.run_once(async_session_factory)
            except Exception as exc:
                processed = 0
                _log.warning("evaluation_poll_failed", error=str(exc))
            if not processed:
                await asyncio.sleep(self.poll_interval_sec)

    async def run_once(self, session_factory) -> int:
        now = _utcnow()
        stale_before = now - timedelta(minutes=15)
        async with session_factory() as session:
            await session.execute(
                update(EvaluationRun)
                .where(
                    EvaluationRun.status == "running",
                    EvaluationRun.claimed_at < stale_before,
                )
                .values(
                    status="retry",
                    claimed_by=None,
                    claimed_at=None,
                    next_retry_time=now,
                    error_message="worker lease expired",
                )
            )
            record = (
                await session.execute(
                    select(EvaluationRun)
                    .where(
                        EvaluationRun.status.in_(["queued", "retry"]),
                        or_(
                            EvaluationRun.next_retry_time.is_(None),
                            EvaluationRun.next_retry_time <= now,
                        ),
                    )
                    .order_by(EvaluationRun.create_time)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if record is None:
                await session.commit()
                return 0
            record.status = "running"
            record.started_at = now
            record.claimed_at = now
            record.claimed_by = self.worker_id
            record.attempts = (record.attempts or 0) + 1
            run_id = record.id
            await session.commit()

        try:
            await execute_evaluation_run(run_id, session_factory)
        except Exception as exc:
            _log.exception(
                "evaluation_run_failed", run_id=run_id, error=str(exc)
            )
            async with session_factory() as session:
                failed = await session.get(EvaluationRun, run_id)
                if failed is not None:
                    failed.status = (
                        "retry" if (failed.attempts or 0) < 3 else "failed"
                    )
                    failed.gate_status = "failed"
                    failed.error_message = (
                        f"{type(exc).__name__}: {exc}"[:2000]
                    )
                    failed.completed_at = _utcnow()
                    if failed.status == "retry":
                        failed.next_retry_time = _utcnow() + timedelta(
                            seconds=min(300, 2 ** (failed.attempts or 1))
                        )
                    await session.commit()
        return 1


async def execute_evaluation_run(run_id: str, session_factory) -> None:
    """Execute one persisted run without holding a database transaction."""
    async with session_factory() as session:
        record = await session.get(EvaluationRun, run_id)
        if record is None:
            return
        config = dict(record.config_json or {})
        scope_id = record.kb_id
        kb = None
        if scope_id != "*":
            kb = await session.get(KnowledgeBase, scope_id)
            if kb is None or kb.deleted:
                raise RuntimeError("evaluation knowledge base is missing")
        persisted_cases = [
            to_evaluation_case(item)
            for item in (
                await session.execute(
                    select(EvaluationDatasetCase).where(
                        EvaluationDatasetCase.tenant_id == record.tenant_id,
                        EvaluationDatasetCase.deleted == 0,
                    )
                )
            ).scalars().all()
        ]

    categories = list(config.get("categories") or [])
    cases = [
        case
        for case in [*load_dataset(_DATASET), *persisted_cases]
        if (
            scope_id == "*"
            or not case.knowledge_base_ids
            or set(case.knowledge_base_ids).issubset({scope_id})
        )
        and (not categories or case.category in categories)
    ]
    top_k = int(config.get("top_k", 5))
    runtime = dict(config.get("_runtime") or {})
    retrieval_scopes = [
        RetrievalScope(
            kb_id=str(item["kb_id"]),
            kb_name=str(item.get("kb_name", "")),
            collection_name=str(item["collection_name"]),
            embedding_model=(
                str(item["embedding_model"])
                if item.get("embedding_model")
                else None
            ),
        )
        for item in runtime.get("retrieval_scopes", [])
    ]
    if not retrieval_scopes and kb is not None:
        retrieval_scopes = [
            RetrievalScope(
                kb_id=kb.id,
                kb_name=kb.name,
                collection_name=(
                    kb.active_collection_name or kb.collection_name
                ),
                embedding_model=kb.embedding_model,
            )
        ]
    if not retrieval_scopes:
        raise RuntimeError("evaluation retrieval scopes are missing")
    pipeline = RAGPipeline()
    cases_by_question = {case.question: case for case in cases}

    async def retrieve(question: str, *, top_k: int):
        case = cases_by_question[question]
        result = await pipeline.run(
            RAGContext(
                question=question,
                kb_id=None if scope_id == "*" else retrieval_scopes[0].kb_id,
                collection_name=(
                    None
                    if scope_id == "*"
                    else retrieval_scopes[0].collection_name
                ),
                user_id=str(runtime.get("user_id", "")),
                tenant_id=str(runtime.get("tenant_id", "default")),
                department_id=str(runtime.get("department_id", "")),
                role=str(runtime.get("role", "user")),
                graph_rag=bool(config.get("graph_rag", False)),
                final_top_k=top_k,
                embedding_model=(
                    None
                    if scope_id == "*"
                    else retrieval_scopes[0].embedding_model
                ),
                retrieval_scopes=retrieval_scopes,
            )
        )
        chunk_ids = [
            source["chunkId"] for source in result.sources
        ][:top_k]
        contexts = [
            str(chunk.get("content", ""))
            for chunk in result.context_chunks[:top_k]
        ]
        contexts.extend(case.injected_contexts)
        answer = ""
        if result.answerable or case.injected_contexts:
            from app.evaluation.generation import generate_answer

            answer = await generate_answer(
                question=question,
                contexts=contexts,
                model_name=result.model_name or "",
                model_base_url=result.model_base_url or "",
                model_api_key=result.model_api_key or "",
            )
        return {
            "chunk_ids": chunk_ids,
            "doc_ids": [
                source.get("documentId", "")
                for source in result.sources[:top_k]
            ],
            "scores": [
                source.get("score", 0)
                for source in result.sources[:top_k]
            ],
            "answerable": result.answerable,
            "answer": answer,
            "contexts": contexts,
            "leaked_chunk_ids": (
                chunk_ids if case.category == "acl_denied" else []
            ),
            "forbidden_answer_patterns": case.forbidden_answer_patterns,
        }

    started = datetime.now(timezone.utc)
    results, metrics = await run_evaluation(
        cases,
        retrieve,
        top_k=top_k,
        concurrency=int(config.get("concurrency", 4)),
        timeout_seconds=float(config.get("timeout_seconds", 30)),
        repetitions=int(config.get("repetitions", 1)),
    )
    by_id = {result.case_id: result for result in results}
    slices: dict[str, dict[str, dict]] = {"category": {}, "difficulty": {}}
    for dimension in slices:
        values = sorted(
            {
                getattr(case, dimension)
                for case in cases
                if case.active and case.id in by_id
            }
        )
        for value in values:
            slice_cases = [
                case
                for case in cases
                if getattr(case, dimension) == value and case.id in by_id
            ]
            slices[dimension][value] = calculate_metrics(
                slice_cases,
                [by_id[case.id] for case in slice_cases],
                top_k=top_k,
            )

    gates = assess_quality_gates(metrics, top_k=top_k)
    async with session_factory() as session:
        record = await session.get(EvaluationRun, run_id)
        if record is None:
            return
        baselines = (
            await session.execute(
                select(EvaluationRun)
                .where(
                    EvaluationRun.tenant_id == record.tenant_id,
                    EvaluationRun.kb_id == record.kb_id,
                    EvaluationRun.status == "completed",
                    EvaluationRun.id != record.id,
                )
                .order_by(desc(EvaluationRun.create_time))
                .limit(30)
            )
        ).scalars().all()
        baseline = next(
            (
                item
                for item in baselines
                if (item.config_json or {}).get("top_k") == top_k
                and (item.config_json or {}).get("graph_rag")
                == bool(config.get("graph_rag", False))
                and sorted(
                    (item.config_json or {}).get("categories") or []
                )
                == sorted(categories)
            ),
            None,
        )
        record.baseline_run_id = baseline.id if baseline else None
        record.deltas_json = (
            {
                name: float(value)
                - float((baseline.metrics_json or {}).get(name, 0))
                for name, value in metrics.items()
                if isinstance(value, (int, float))
            }
            if baseline
            else {}
        )
        record.results_json = [
            {
                **result.to_dict(),
                "question": next(
                    case.question
                    for case in cases
                    if case.id == result.case_id
                ),
                "category": next(
                    case.category
                    for case in cases
                    if case.id == result.case_id
                ),
                "difficulty": next(
                    case.difficulty
                    for case in cases
                    if case.id == result.case_id
                ),
                "case_metrics": calculate_case_metrics(
                    next(
                        case
                        for case in cases
                        if case.id == result.case_id
                    ),
                    result,
                    top_k=top_k,
                ),
            }
            for result in results
        ]
        record.status = "completed"
        record.gate_status = gates["status"]
        record.metrics_json = metrics
        record.slices_json = slices
        record.gates_json = gates
        record.duration_ms = int(
            (datetime.now(timezone.utc) - started).total_seconds() * 1000
        )
        record.completed_at = _utcnow()
        record.claimed_by = None
        record.claimed_at = None
        record.next_retry_time = None
        record.error_message = None
        await session.commit()
