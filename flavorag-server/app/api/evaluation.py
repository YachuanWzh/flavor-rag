"""Durable retrieval-and-generation evaluation APIs."""

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
from app.evaluation.runner import load_dataset
from app.models import (
    EvaluationRun,
    KnowledgeBase,
    KnowledgeDocument,
    Message,
    MessageFeedback,
    User,
)
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
        "attempts": item.attempts or 0,
        "errorMessage": item.error_message,
        "createdAt": (
            item.create_time.isoformat() if item.create_time else None
        ),
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
    negative_statement = (
        select(MessageFeedback.id)
        .join(Message, Message.id == MessageFeedback.message_id)
        .where(
            MessageFeedback.deleted == 0,
            MessageFeedback.vote == -1,
        )
    )
    if user.role not in {"admin", "tenant_admin", "system_admin"}:
        negative_statement = negative_statement.where(
            Message.user_id == user.id
        )
    negative_count = len((await db.execute(negative_statement)).all())
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
            "categories": dict(Counter(case.category for case in active)),
            "difficulties": dict(Counter(case.difficulty for case in active)),
            "negativeFeedbackCandidates": negative_count,
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
    rows = list(
        reversed(
            (
                await db.execute(
                    statement.order_by(desc(EvaluationRun.create_time)).limit(
                        min(90, max(2, limit))
                    )
                )
            ).scalars().all()
        )
    )
    alerts = []
    if len(rows) >= 2:
        previous, current = rows[-2], rows[-1]
        common = set(previous.metrics_json or {}) & set(
            current.metrics_json or {}
        )
        for metric in sorted(common):
            if not metric.startswith(
                ("quality_score", "recall@", "ndcg@", "mrr@")
            ):
                continue
            before = float((previous.metrics_json or {}).get(metric, 0))
            after = float((current.metrics_json or {}).get(metric, 0))
            if after - before <= -0.05:
                alerts.append(
                    {
                        "severity": (
                            "critical"
                            if after - before <= -0.10
                            else "warning"
                        ),
                        "metric": metric,
                        "delta": after - before,
                        "message": f"{metric} 较上次下降 {abs(after - before):.1%}",
                    }
                )
    return {
        "code": "0",
        "data": {
            "points": [
                {
                    "id": item.id,
                    "timestamp": (
                        item.create_time.isoformat()
                        if item.create_time
                        else None
                    ),
                    "kbName": item.kb_name,
                    "gateStatus": item.gate_status,
                    "metrics": item.metrics_json or {},
                }
                for item in rows
            ],
            "alerts": alerts,
        },
    }


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
        question = (
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
        if not question:
            continue
        sources = answer.sources or []
        candidates.append(
            {
                "id": f"feedback-{feedback.id}",
                "question": question.content,
                "answer": answer.content,
                "reason": feedback.reason,
                "comment": feedback.comment,
                "sources": sources,
                "suggestedCase": {
                    "id": f"feedback-{feedback.id}",
                    "question": question.content,
                    # Negative production retrieval is review material, not
                    # ground truth. A human must label expected positives.
                    "expected_chunk_ids": [],
                    "expected_doc_ids": [],
                    "retrieved_chunk_ids_for_review": [
                        source.get("chunkId")
                        for source in sources
                        if source.get("chunkId")
                    ],
                    "retrieved_doc_ids_for_review": [
                        source.get("documentId")
                        for source in sources
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

    expected_doc_ids = {
        doc_id
        for case in cases
        if case.active
        for doc_id in case.expected_doc_ids
    }
    corpus_rows = (
        await db.execute(
            select(
                KnowledgeDocument.id,
                KnowledgeDocument.content_hash,
                KnowledgeDocument.active_generation,
            ).where(
                KnowledgeDocument.kb_id == kb.id,
                KnowledgeDocument.deleted == 0,
                KnowledgeDocument.enabled != 0,
            )
        )
    ).all()
    bound_doc_ids = {row.id for row in corpus_rows}
    missing = expected_doc_ids - bound_doc_ids
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "evaluation dataset is not bound to the selected corpus; "
                f"{len(missing)} expected document(s) are missing"
            ),
        )

    corpus_snapshot = hashlib.sha256(
        "\n".join(
            f"{row.id}:{row.content_hash or ''}:{row.active_generation or ''}"
            for row in sorted(corpus_rows, key=lambda item: item.id)
        ).encode()
    ).hexdigest()[:16]
    generation_by_doc = {
        row.id: row.active_generation or "" for row in corpus_rows
    }
    for case in cases:
        if (
            case.active
            and case.corpus_snapshot
            and case.corpus_snapshot != corpus_snapshot
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"case {case.id} targets corpus snapshot "
                    f"{case.corpus_snapshot}, selected corpus is "
                    f"{corpus_snapshot}"
                ),
            )
        if (
            case.active
            and case.document_generation
            and any(
                generation_by_doc.get(doc_id) != case.document_generation
                for doc_id in case.expected_doc_ids
            )
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"case {case.id} targets document generation "
                    f"{case.document_generation}, but the selected corpus "
                    "contains a different active generation"
                ),
            )

    config = {
        **request.model_dump(),
        "corpus_snapshot": corpus_snapshot,
        "index_generation": kb.active_index_generation,
        "embedding_model": kb.embedding_model,
        "prompt_version": "rag-safe-evidence-v0.0.5",
        "_runtime": {
            "user_id": user.id,
            "tenant_id": user.tenant_id or "default",
            "department_id": user.department_id or "",
            "role": user.role,
        },
    }
    record = EvaluationRun(
        tenant_id=user.tenant_id or "default",
        kb_id=kb.id,
        kb_name=kb.name,
        dataset_version=_dataset_version(),
        status="queued",
        gate_status="pending",
        config_json=config,
        created_by=user.id,
        started_at=None,
    )
    db.add(record)
    await db.flush()
    return {
        "code": "0",
        "message": "queued",
        "data": _run_payload(record),
    }
