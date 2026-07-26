"""Retrieval evaluation APIs: replay, quality gates, history and trend analysis."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_db
from app.evaluation.runner import (
    assess_quality_gates,
    calculate_case_metrics,
    calculate_metrics,
    load_dataset,
    run_evaluation,
)
from app.models import EvaluationRun, KnowledgeBase, Message, MessageFeedback, User
from app.rag.pipeline import RAGContext, RAGPipeline
from app.security.access import Permission
from app.security.service import kb_access_predicate, principal_from_user

router = APIRouter(prefix="/api/admin/evaluation", tags=["evaluation"])
_DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "minimal.jsonl"


class EvaluationRunRequest(BaseModel):
    kb_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    categories: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=4, ge=1, le=12)
    timeout_seconds: float = Field(default=30, ge=5, le=120)
    repetitions: int = Field(default=1, ge=1, le=5)
    graph_rag: bool = False
    label: str | None = Field(default=None, max_length=80)


def _dataset_version() -> str:
    return hashlib.sha256(_DATASET.read_bytes()).hexdigest()[:12]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _run_payload(item: EvaluationRun, *, include_results: bool = False) -> dict:
    payload = {
        "id": item.id,
        "knowledgeBase": {"id": item.kb_id, "name": item.kb_name},
        "datasetVersion": item.dataset_version,
        "status": item.status,
        "gateStatus": item.gate_status,
        "config": item.config_json or {},
        "metrics": item.metrics_json or {},
        "bySlice": item.slices_json or {},
        "gates": item.gates_json or {},
        "baselineRunId": item.baseline_run_id,
        "deltas": item.deltas_json or {},
        "durationMs": item.duration_ms or 0,
        "createdAt": item.create_time.isoformat() if item.create_time else None,
    }
    if include_results:
        payload["results"] = item.results_json or []
    return payload


@router.get("/overview")
async def overview(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cases = load_dataset(_DATASET)
    active = [case for case in cases if case.active]
    categories = Counter(case.category for case in active)
    difficulties = Counter(case.difficulty for case in active)
    negative_count = (
        await db.execute(
            select(MessageFeedback.id)
            .join(Message, Message.id == MessageFeedback.message_id)
            .where(
                MessageFeedback.deleted == 0,
                MessageFeedback.vote == -1,
                Message.user_id == user.id
                if user.role not in {"admin", "tenant_admin", "system_admin"}
                else True,
            )
        )
    ).all()
    latest = (
        await db.execute(
            select(EvaluationRun)
            .where(
                EvaluationRun.tenant_id == (user.tenant_id or "default"),
                EvaluationRun.status == "completed",
            )
            .order_by(desc(EvaluationRun.create_time))
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "code": "0",
        "data": {
            "datasetVersion": _dataset_version(),
            "caseCount": len(cases),
            "activeCaseCount": len(active),
            "answerableCount": sum(case.answerable for case in active),
            "negativeCaseCount": sum(not case.answerable for case in active),
            "categories": dict(categories),
            "difficulties": dict(difficulties),
            "negativeFeedbackCandidates": len(negative_count),
            "latestRun": _run_payload(latest) if latest else None,
            "cases": [
                {
                    "id": case.id,
                    "question": case.question,
                    "category": case.category,
                    "difficulty": case.difficulty,
                    "tags": case.tags,
                    "answerable": case.answerable,
                    "active": case.active,
                    "inactiveReason": case.inactive_reason,
                    "expectedChunkIds": case.expected_chunk_ids,
                    "expectedDocIds": case.expected_doc_ids,
                }
                for case in cases
            ],
        },
    }


@router.get("/runs")
async def list_runs(
    kb_id: str | None = None,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    statement = select(EvaluationRun).where(
        EvaluationRun.tenant_id == (user.tenant_id or "default")
    )
    if kb_id:
        statement = statement.where(EvaluationRun.kb_id == kb_id)
    rows = (
        await db.execute(
            statement.order_by(desc(EvaluationRun.create_time)).limit(
                min(100, max(1, limit))
            )
        )
    ).scalars().all()
    return {"code": "0", "data": [_run_payload(item) for item in rows]}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = (
        await db.execute(
            select(EvaluationRun).where(
                EvaluationRun.id == run_id,
                EvaluationRun.tenant_id == (user.tenant_id or "default"),
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="评测运行不存在")
    return {"code": "0", "data": _run_payload(item, include_results=True)}


@router.get("/trend")
async def trend(
    kb_id: str | None = None,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    statement = select(EvaluationRun).where(
        EvaluationRun.tenant_id == (user.tenant_id or "default"),
        EvaluationRun.status == "completed",
    )
    if kb_id:
        statement = statement.where(EvaluationRun.kb_id == kb_id)
    rows = (
        await db.execute(
            statement.order_by(desc(EvaluationRun.create_time)).limit(
                min(90, max(2, limit))
            )
        )
    ).scalars().all()
    rows = list(reversed(rows))
    points = [
        {
            "id": item.id,
            "timestamp": item.create_time.isoformat() if item.create_time else None,
            "kbName": item.kb_name,
            "gateStatus": item.gate_status,
            "metrics": item.metrics_json or {},
        }
        for item in rows
    ]
    alerts = []
    if len(rows) >= 2:
        previous, current = rows[-2], rows[-1]
        ranked_metrics = []
        for prefix in ("recall@", "ndcg@", "mrr@"):
            current_name = next(
                (
                    name
                    for name in (current.metrics_json or {})
                    if name.startswith(prefix)
                ),
                None,
            )
            if current_name and current_name in (previous.metrics_json or {}):
                ranked_metrics.append(current_name)
        for metric in ("quality_score", *ranked_metrics):
            before = float((previous.metrics_json or {}).get(metric, 0))
            after = float((current.metrics_json or {}).get(metric, 0))
            if after - before <= -0.05:
                alerts.append(
                    {
                        "severity": "critical" if after - before <= -0.10 else "warning",
                        "metric": metric,
                        "delta": after - before,
                        "message": f"{metric} 较上次下降 {abs(after - before):.1%}",
                    }
                )
        latency_delta = float((current.metrics_json or {}).get("latency_p95_ms", 0)) - float(
            (previous.metrics_json or {}).get("latency_p95_ms", 0)
        )
        if latency_delta >= 500:
            alerts.append(
                {
                    "severity": "warning",
                    "metric": "latency_p95_ms",
                    "delta": latency_delta,
                    "message": f"P95 时延较上次增加 {latency_delta:.0f}ms",
                }
            )
    return {"code": "0", "data": {"points": points, "alerts": alerts}}


@router.get("/feedback-candidates")
async def feedback_candidates(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    statement = (
        select(MessageFeedback, Message)
        .join(Message, Message.id == MessageFeedback.message_id)
        .where(MessageFeedback.deleted == 0, MessageFeedback.vote == -1)
        .order_by(desc(MessageFeedback.create_time))
        .limit(min(200, max(1, limit)))
    )
    if user.role not in {"admin", "tenant_admin", "system_admin"}:
        statement = statement.where(MessageFeedback.user_id == user.id)
    rows = (await db.execute(statement)).all()
    candidates = []
    for feedback, answer in rows:
        previous = (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == answer.conversation_id,
                    Message.role == "user",
                    Message.create_time <= answer.create_time,
                    Message.deleted == 0,
                )
                .order_by(desc(Message.create_time))
                .limit(1)
            )
        ).scalar_one_or_none()
        if not previous:
            continue
        candidates.append(
            {
                "id": f"feedback-{feedback.id}",
                "question": previous.content,
                "answer": answer.content,
                "reason": feedback.reason,
                "comment": feedback.comment,
                "sources": answer.sources or [],
                "suggestedCase": {
                    "id": f"feedback-{feedback.id}",
                    "question": previous.content,
                    "expected_chunk_ids": [
                        source.get("chunkId")
                        for source in (answer.sources or [])
                        if source.get("chunkId")
                    ],
                    "expected_doc_ids": [
                        source.get("documentId")
                        for source in (answer.sources or [])
                        if source.get("documentId")
                    ],
                    "category": "feedback_review",
                    "difficulty": "medium",
                    "tags": ["production_feedback"],
                    "answerable": True,
                    "active": False,
                },
            }
        )
    return {"code": "0", "data": candidates}


@router.post("/run")
async def run(
    request: EvaluationRunRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    principal = principal_from_user(user)
    kb_statement = select(KnowledgeBase).where(
        kb_access_predicate(principal, Permission.READ)
    )
    if request.kb_id:
        kb_statement = kb_statement.where(KnowledgeBase.id == request.kb_id)
    kb = (await db.execute(kb_statement.limit(1))).scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="没有可用于评测的知识库")

    cases = [
        case
        for case in load_dataset(_DATASET)
        if not request.categories or case.category in request.categories
    ]
    if not any(case.active for case in cases):
        raise HTTPException(status_code=400, detail="所选范围没有启用的评测案例")

    config = request.model_dump()
    record = EvaluationRun(
        tenant_id=user.tenant_id or "default",
        kb_id=kb.id,
        kb_name=kb.name,
        dataset_version=_dataset_version(),
        status="running",
        gate_status="pending",
        config_json=config,
        created_by=user.id,
        started_at=_utcnow(),
    )
    db.add(record)
    await db.flush()

    pipeline = RAGPipeline()
    cases_by_question = {case.question: case for case in cases}

    async def retrieve(question: str, *, top_k: int):
        result = await pipeline.run(
            RAGContext(
                question=question,
                kb_id=kb.id,
                collection_name=kb.collection_name,
                user_id=user.id,
                tenant_id=user.tenant_id or "default",
                department_id=user.department_id or "",
                role=user.role,
                graph_rag=request.graph_rag,
            )
        )
        chunk_ids = [source["chunkId"] for source in result.sources][:top_k]
        case = cases_by_question[question]
        return {
            "chunk_ids": chunk_ids,
            "doc_ids": [
                source.get("documentId", "") for source in result.sources[:top_k]
            ],
            "scores": [source.get("score", 0) for source in result.sources[:top_k]],
            "answerable": result.answerable,
            "leaked_chunk_ids": (
                chunk_ids if case.category == "acl_denied" else []
            ),
        }

    started = datetime.now(timezone.utc)
    try:
        results, metrics = await run_evaluation(
            cases,
            retrieve,
            top_k=request.top_k,
            concurrency=request.concurrency,
            timeout_seconds=request.timeout_seconds,
            repetitions=request.repetitions,
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
                    top_k=request.top_k,
                )
        gates = assess_quality_gates(metrics, top_k=request.top_k)
        baseline_candidates = (
            await db.execute(
                select(EvaluationRun)
                .where(
                    EvaluationRun.tenant_id == (user.tenant_id or "default"),
                    EvaluationRun.kb_id == kb.id,
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
                for item in baseline_candidates
                if (item.config_json or {}).get("top_k") == request.top_k
                and (item.config_json or {}).get("graph_rag") == request.graph_rag
                and sorted((item.config_json or {}).get("categories") or [])
                == sorted(request.categories)
            ),
            None,
        )
        deltas = {
            name: float(value) - float((baseline.metrics_json or {}).get(name, 0))
            for name, value in metrics.items()
            if isinstance(value, (int, float))
        } if baseline else {}
        detailed_results = []
        for result in results:
            case = next(case for case in cases if case.id == result.case_id)
            detailed_results.append(
                {
                    **result.to_dict(),
                    "question": case.question,
                    "category": case.category,
                    "difficulty": case.difficulty,
                    "expected_chunk_ids": case.expected_chunk_ids,
                    "case_metrics": calculate_case_metrics(
                        case, result, top_k=request.top_k
                    ),
                }
            )
        record.status = "completed"
        record.gate_status = gates["status"]
        record.metrics_json = metrics
        record.slices_json = slices
        record.gates_json = gates
        record.baseline_run_id = baseline.id if baseline else None
        record.deltas_json = deltas
        record.results_json = detailed_results
        record.duration_ms = int(
            (datetime.now(timezone.utc) - started).total_seconds() * 1000
        )
        record.completed_at = _utcnow()
        await db.flush()
        return {
            "code": "0",
            "data": _run_payload(record, include_results=True),
        }
    except Exception:
        record.status = "failed"
        record.gate_status = "failed"
        record.duration_ms = int(
            (datetime.now(timezone.utc) - started).total_seconds() * 1000
        )
        record.completed_at = _utcnow()
        await db.flush()
        raise
