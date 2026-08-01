"""Durable, multi-replica-safe evaluation job worker."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, or_, select, update

from app.config.logging_config import get_logger
from app.evaluation import DATASET_PATH
from app.evaluation.cases import to_evaluation_case
from app.evaluation.runner import (
    assess_quality_gates,
    calculate_case_metrics,
    calculate_metrics,
    is_refusal_response,
    load_dataset,
    run_evaluation,
)
from app.models import (
    EvaluationDatasetCase,
    EvaluationRun,
    KnowledgeBase,
    KnowledgeChunk,
)
from app.rag.pipeline import RAGContext, RAGPipeline, RetrievalScope
from app.security.access import Permission, Principal
from app.security.service import kb_access_predicate

_log = get_logger("flavorag.evaluation.worker")
_DATASET = DATASET_PATH


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _scopes_for_case(
    case,
    retrieval_scopes: list[RetrievalScope],
) -> list[RetrievalScope]:
    """Apply the dataset's declared corpus scope to one evaluation case."""
    if not case.knowledge_base_ids:
        return retrieval_scopes
    by_id = {scope.kb_id: scope for scope in retrieval_scopes}
    missing = [
        kb_id for kb_id in case.knowledge_base_ids if kb_id not in by_id
    ]
    if missing:
        raise RuntimeError(
            f"case {case.id} references unavailable knowledge base(s): "
            + ", ".join(missing)
        )
    return [by_id[kb_id] for kb_id in case.knowledge_base_ids]


class EvaluationJobWorker:
    """Claim persisted evaluation runs and resume them after process failure."""

    def __init__(
        self,
        poll_interval_sec: int = 5,
        *,
        lease_timeout_sec: int = 15 * 60,
        heartbeat_interval_sec: int = 60,
    ):
        self.poll_interval_sec = poll_interval_sec
        self.lease_timeout_sec = lease_timeout_sec
        self.heartbeat_interval_sec = heartbeat_interval_sec
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

    async def _heartbeat(self, run_id: str, session_factory) -> None:
        """Renew a long-running evaluation lease until ownership is lost."""
        while True:
            await asyncio.sleep(self.heartbeat_interval_sec)
            try:
                async with session_factory() as session:
                    result = await session.execute(
                        update(EvaluationRun)
                        .where(
                            EvaluationRun.id == run_id,
                            EvaluationRun.status == "running",
                            EvaluationRun.claimed_by == self.worker_id,
                        )
                        .values(claimed_at=_utcnow())
                    )
                    await session.commit()
                    if not result.rowcount:
                        _log.warning(
                            "evaluation_lease_lost",
                            run_id=run_id,
                            worker_id=self.worker_id,
                        )
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A transient database failure should not terminate the
                # evaluation task. A subsequent heartbeat can still renew the
                # lease before the stale-worker timeout.
                _log.warning(
                    "evaluation_heartbeat_failed",
                    run_id=run_id,
                    error=str(exc),
                )

    async def run_once(self, session_factory) -> int:
        now = _utcnow()
        stale_before = now - timedelta(seconds=self.lease_timeout_sec)
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
            attempts = record.attempts
            await session.commit()

        heartbeat = asyncio.create_task(
            self._heartbeat(run_id, session_factory),
            name=f"evaluation-heartbeat-{run_id}",
        )
        try:
            await execute_evaluation_run(
                run_id,
                session_factory,
                expected_worker_id=self.worker_id,
            )
        except Exception as exc:
            _log.exception(
                "evaluation_run_failed", run_id=run_id, error=str(exc)
            )
            async with session_factory() as session:
                retrying = attempts < 3
                now = _utcnow()
                result = await session.execute(
                    update(EvaluationRun)
                    .where(
                        EvaluationRun.id == run_id,
                        EvaluationRun.status == "running",
                        EvaluationRun.claimed_by == self.worker_id,
                    )
                    .values(
                        status="retry" if retrying else "failed",
                        gate_status="failed",
                        error_message=(
                            f"{type(exc).__name__}: {exc}"[:2000]
                        ),
                        completed_at=None if retrying else now,
                        next_retry_time=(
                            now
                            + timedelta(seconds=min(300, 2 ** (attempts + 1)))
                            if retrying
                            else None
                        ),
                        claimed_by=None,
                        claimed_at=None,
                    )
                )
                await session.commit()
                if not result.rowcount:
                    _log.warning(
                        "evaluation_failure_ignored_after_lease_loss",
                        run_id=run_id,
                        worker_id=self.worker_id,
                    )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return 1


async def execute_evaluation_run(
    run_id: str,
    session_factory,
    *,
    expected_worker_id: str,
) -> None:
    """Execute one persisted run without holding a database transaction."""
    async with session_factory() as session:
        record = await session.get(EvaluationRun, run_id)
        if record is None:
            return
        if (
            record.status != "running"
            or record.claimed_by != expected_worker_id
        ):
            _log.warning(
                "evaluation_execution_skipped_after_lease_loss",
                run_id=run_id,
                worker_id=expected_worker_id,
            )
            return
        config = dict(record.config_json or {})
        scope_id = record.kb_id
        runtime = dict(config.get("_runtime") or {})
        principal = Principal(
            user_id=str(runtime.get("user_id") or record.created_by or ""),
            tenant_id=str(runtime.get("tenant_id") or record.tenant_id),
            department_id=str(runtime.get("department_id") or ""),
            role=str(runtime.get("role") or "user"),
        )
        runtime_scope_items = list(runtime.get("retrieval_scopes") or [])
        requested_scope_ids = list(
            dict.fromkeys(
                str(item.get("kb_id") or "")
                for item in runtime_scope_items
                if item.get("kb_id")
            )
        )
        if not requested_scope_ids:
            requested_scope_ids = list(
                dict.fromkeys(
                    str(kb_id)
                    for kb_id in (config.get("index_generations") or {})
                    if kb_id
                )
            )
        if not requested_scope_ids and scope_id != "*":
            requested_scope_ids = [scope_id]

        kb_statement = select(KnowledgeBase).where(
            kb_access_predicate(principal, Permission.READ)
        )
        if requested_scope_ids:
            kb_statement = kb_statement.where(
                KnowledgeBase.id.in_(requested_scope_ids)
            )
        kb_rows = list((await session.execute(kb_statement)).scalars().all())
        kb_by_id = {kb.id: kb for kb in kb_rows}
        if requested_scope_ids:
            missing_scope_ids = [
                kb_id for kb_id in requested_scope_ids if kb_id not in kb_by_id
            ]
            if missing_scope_ids:
                raise RuntimeError(
                    "evaluation retrieval scope contains missing or inaccessible "
                    "knowledge base(s): " + ", ".join(missing_scope_ids)
                )
            ordered_kbs = [kb_by_id[kb_id] for kb_id in requested_scope_ids]
        else:
            ordered_kbs = kb_rows
        retrieval_scopes = [
            RetrievalScope(
                kb_id=kb.id,
                kb_name=kb.name,
                collection_name=(
                    kb.active_collection_name or kb.collection_name
                ),
                embedding_model=kb.embedding_model,
            )
            for kb in ordered_kbs
        ]
        if not retrieval_scopes:
            raise RuntimeError("evaluation retrieval scopes are missing")
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

    # ── Dynamic hash enrichment ──
    # Keep labels stable across an equivalent re-ingestion while retaining
    # exact chunk IDs as the primary match key.
    all_expected_ids: list[str] = []
    for case in cases:
        if case.expected_chunk_ids:
            all_expected_ids.extend(case.expected_chunk_ids)
    if all_expected_ids:
        allowed_scope_ids = [scope.kb_id for scope in retrieval_scopes]
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(KnowledgeChunk.id, KnowledgeChunk.content_hash).where(
                        KnowledgeChunk.id.in_(all_expected_ids),
                        KnowledgeChunk.tenant_id == record.tenant_id,
                        KnowledgeChunk.kb_id.in_(allowed_scope_ids),
                    )
                )
            ).all()
        id_to_hash = {row[0]: row[1] for row in rows if row[1]}
        enriched: list = []
        for case in cases:
            if case.expected_chunk_ids:
                hashes = [
                    (
                        case.expected_chunk_hashes[index]
                        if index < len(case.expected_chunk_hashes)
                        and case.expected_chunk_hashes[index]
                        else id_to_hash.get(chunk_id, "")
                    )
                    for index, chunk_id in enumerate(case.expected_chunk_ids)
                ]
                enriched.append(
                    replace(case, expected_chunk_hashes=hashes)
                )
            else:
                enriched.append(case)
        cases = enriched

    pipeline = RAGPipeline()
    cases_by_question = {case.question: case for case in cases}

    async def retrieve(question: str, *, top_k: int):
        case = cases_by_question[question]
        case_scopes = _scopes_for_case(case, retrieval_scopes)
        single_scope = len(case_scopes) == 1
        retrieval_started = time.monotonic()
        result = await pipeline.run(
            RAGContext(
                question=question,
                kb_id=case_scopes[0].kb_id if single_scope else None,
                collection_name=(
                    case_scopes[0].collection_name if single_scope else None
                ),
                user_id=str(runtime.get("user_id", "")),
                tenant_id=str(runtime.get("tenant_id", "default")),
                department_id=str(runtime.get("department_id", "")),
                role=str(runtime.get("role", "user")),
                graph_rag=bool(config.get("graph_rag", False)),
                final_top_k=top_k,
                embedding_model=(
                    case_scopes[0].embedding_model if single_scope else None
                ),
                retrieval_scopes=case_scopes,
            )
        )
        retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)
        chunk_ids = [
            source["chunkId"] for source in result.sources
        ][:top_k]
        chunk_hashes = [
            source.get("contentHash", "") for source in result.sources
        ][:top_k]
        contexts = [
            str(chunk.get("content", ""))
            for chunk in result.context_chunks[:top_k]
        ]
        contexts.extend(case.injected_contexts)
        answer = str(result.direct_response or "")
        generation_ms = 0
        if not answer and (result.answerable or case.injected_contexts):
            from app.evaluation.generation import generate_answer

            gen_started = time.monotonic()
            answer = await generate_answer(
                question=question,
                contexts=contexts,
                model_name=result.model_name or "",
                model_base_url=result.model_base_url or "",
                model_api_key=result.model_api_key or "",
            )
            generation_ms = int((time.monotonic() - gen_started) * 1000)
        allowed_kb_ids = {scope.kb_id for scope in case_scopes}
        leaked_chunk_ids = [
            str(source.get("chunkId") or "")
            for source in result.sources[:top_k]
            if str(source.get("kbId") or "") not in allowed_kb_ids
        ]
        return {
            "chunk_ids": chunk_ids,
            "chunk_hashes": chunk_hashes,
            "doc_ids": [
                source.get("documentId", "")
                for source in result.sources[:top_k]
            ],
            "scores": [
                source.get("score", 0)
                for source in result.sources[:top_k]
            ],
            "answerable": bool(
                result.answerable and not is_refusal_response(answer)
            ),
            "answer": answer,
            "contexts": contexts,
            "leaked_chunk_ids": leaked_chunk_ids,
            "forbidden_answer_patterns": case.forbidden_answer_patterns,
            "retrieval_latency_ms": retrieval_ms,
            "generation_latency_ms": generation_ms,
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
        if (
            record.status != "running"
            or record.claimed_by != expected_worker_id
        ):
            _log.warning(
                "evaluation_result_discarded_after_lease_loss",
                run_id=run_id,
                worker_id=expected_worker_id,
            )
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
