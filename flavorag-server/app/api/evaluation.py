"""Durable retrieval-and-generation evaluation APIs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.dependencies import get_current_user
from app.database.session import get_db
from app.evaluation import DATASET_PATH
from app.evaluation.runner import load_dataset
from app.models import (
    EvaluationRun,
    EvaluationDatasetCase,
    KnowledgeBase,
    KnowledgeDocument,
    Message,
    MessageFeedback,
    User,
)
from app.evaluation.cases import (
    calculate_quality_score,
    case_label,
    ensure_base_case,
    promote_to_golden,
    to_evaluation_case,
)
from app.security.access import Permission
from app.security.service import kb_access_predicate, principal_from_user

router = APIRouter(prefix="/api/admin/evaluation", tags=["evaluation"])
_DATASET = DATASET_PATH


class EvaluationRunRequest(BaseModel):
    kb_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    categories: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=4, ge=1, le=12)
    timeout_seconds: float = Field(default=120, ge=5, le=300)
    repetitions: int = Field(default=1, ge=1, le=5)
    graph_rag: bool = False
    label: str | None = Field(default=None, max_length=80)


def _dataset_version(cases: list | None = None) -> str:
    digest = hashlib.sha256(_DATASET.read_bytes())
    for case in sorted(cases or [], key=lambda item: item.id):
        digest.update(
            json.dumps(
                {
                    "id": case.id,
                    "active": case.active,
                    "expected_chunk_ids": case.expected_chunk_ids,
                    "expected_doc_ids": case.expected_doc_ids,
                    "tags": case.tags,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )
    return digest.hexdigest()[:12]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cases_for_scope(cases: list, kb_id: str | None) -> list:
    if not kb_id or kb_id == "*":
        return cases
    return [
        case
        for case in cases
        if not case.knowledge_base_ids
        or set(case.knowledge_base_ids).issubset({kb_id})
    ]


async def _all_cases(
    db: AsyncSession,
    *,
    tenant_id: str,
    kb_id: str | None = None,
) -> list:
    persisted = (
        await db.execute(
            select(EvaluationDatasetCase).where(
                EvaluationDatasetCase.tenant_id == tenant_id,
                EvaluationDatasetCase.deleted == 0,
            )
        )
    ).scalars().all()
    combined = [*load_dataset(_DATASET), *(to_evaluation_case(row) for row in persisted)]
    return _cases_for_scope(combined, kb_id)


def _corpus_snapshot(rows: list) -> str:
    return hashlib.sha256(
        "\n".join(
            f"{row.id}:{row.content_hash or ''}:{row.active_generation or ''}"
            for row in sorted(rows, key=lambda item: item.id)
        ).encode()
    ).hexdigest()[:16]


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
    kb_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cases = await _all_cases(
        db,
        tenant_id=user.tenant_id or "default",
        kb_id=kb_id,
    )
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
            "datasetVersion": _dataset_version(cases),
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
                    "knowledgeBaseIds": case.knowledge_base_ids,
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


@router.get("/questions")
async def list_question_assets(
    q: str | None = None,
    user_id: str | None = None,
    label: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List persisted user questions with their answer, feedback and case state."""
    question = aliased(Message, name="question")
    answer = aliased(Message, name="answer")
    feedback = aliased(MessageFeedback, name="feedback")
    asset = aliased(EvaluationDatasetCase, name="asset")
    answer_id = (
        select(answer.id)
        .where(
            answer.conversation_id == question.conversation_id,
            answer.user_id == question.user_id,
            answer.role == "assistant",
            answer.deleted == 0,
            answer.create_time >= question.create_time,
        )
        .order_by(answer.create_time, answer.id)
        .limit(1)
        .correlate(question)
        .scalar_subquery()
    )
    statement = (
        select(question, answer, feedback, asset, User)
        .join(User, User.id == question.user_id)
        .outerjoin(answer, answer.id == answer_id)
        .outerjoin(
            feedback,
            (feedback.message_id == answer.id)
            & (feedback.user_id == question.user_id)
            & (feedback.deleted == 0),
        )
        .outerjoin(
            asset,
            (asset.source_question_id == question.id)
            & (asset.tenant_id == question.tenant_id)
            & (asset.deleted == 0),
        )
        .where(question.role == "user", question.deleted == 0)
    )
    if user.role == "system_admin":
        pass
    elif user.role in {"admin", "tenant_admin"}:
        statement = statement.where(
            question.tenant_id == (user.tenant_id or "default")
        )
    else:
        statement = statement.where(question.user_id == user.id)
    if user_id:
        statement = statement.where(question.user_id == user_id)
    if q:
        statement = statement.where(question.content.ilike(f"%{q.strip()}%"))
    normalized_label = (label or "all").lower()
    if normalized_label == "bad":
        statement = statement.where(feedback.vote == -1)
    elif normalized_label == "good":
        statement = statement.where(feedback.vote == 1)
    elif normalized_label == "unrated":
        statement = statement.where(feedback.id.is_(None))
    elif normalized_label == "golden":
        statement = statement.where(asset.review_status == "approved")

    page = max(1, page)
    page_size = min(100, max(1, page_size))
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = int((await db.execute(count_statement)).scalar_one())
    rows = (
        await db.execute(
            statement.order_by(desc(question.create_time), desc(question.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = []
    for question_row, answer_row, feedback_row, asset_row, owner in rows:
        if answer_row is not None and asset_row is None:
            asset_row = await ensure_base_case(
                db,
                question=question_row,
                answer=answer_row,
                feedback=feedback_row,
            )
        vote = feedback_row.vote if feedback_row else None
        review_status = asset_row.review_status if asset_row else None
        items.append(
            {
                "id": question_row.id,
                "conversationId": question_row.conversation_id,
                "user": {"id": owner.id, "username": owner.username},
                "question": question_row.content,
                "answer": (
                    {
                        "id": answer_row.id,
                        "content": answer_row.content,
                        "sourceCount": len(answer_row.sources or []),
                    }
                    if answer_row
                    else None
                ),
                "feedback": (
                    {
                        "vote": vote,
                        "reason": feedback_row.reason,
                        "comment": feedback_row.comment,
                    }
                    if feedback_row
                    else None
                ),
                "qualityScore": (
                    round(float(asset_row.quality_score))
                    if asset_row
                    else calculate_quality_score(answer_row, vote)
                ),
                "label": case_label(vote, review_status),
                "dataset": (
                    {
                        "id": asset_row.id,
                        "caseType": asset_row.case_type,
                        "reviewStatus": asset_row.review_status,
                        "active": bool(asset_row.active),
                    }
                    if asset_row
                    else None
                ),
                "createdAt": question_row.create_time.isoformat()
                if question_row.create_time
                else None,
            }
        )
    await db.flush()
    return {
        "code": "0",
        "data": {
            "items": items,
            "page": page,
            "pageSize": page_size,
            "total": total,
        },
    }


@router.post("/questions/{question_id}/golden")
async def generate_golden_case(
    question_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    question = await db.get(Message, question_id)
    if question is None or question.deleted or question.role != "user":
        raise HTTPException(status_code=404, detail="用户问题不存在")
    if user.role != "system_admin" and question.tenant_id != (user.tenant_id or "default"):
        raise HTTPException(status_code=404, detail="用户问题不存在")
    if user.role not in {"admin", "tenant_admin", "system_admin"} and question.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权处理其他用户的问题")

    answer = (
        await db.execute(
            select(Message)
            .where(
                Message.conversation_id == question.conversation_id,
                Message.user_id == question.user_id,
                Message.role == "assistant",
                Message.deleted == 0,
                Message.create_time >= question.create_time,
            )
            .order_by(Message.create_time, Message.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if answer is None:
        raise HTTPException(status_code=409, detail="问题尚未生成完整回答，暂不能创建测试案例")
    feedback = (
        await db.execute(
            select(MessageFeedback).where(
                MessageFeedback.message_id == answer.id,
                MessageFeedback.user_id == question.user_id,
                MessageFeedback.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    case = await ensure_base_case(
        db,
        question=question,
        answer=answer,
        feedback=feedback,
    )
    if case.retrieved_doc_ids:
        case.knowledge_base_ids = list(
            dict.fromkeys(
                (
                    await db.execute(
                        select(KnowledgeDocument.kb_id).where(
                            KnowledgeDocument.id.in_(case.retrieved_doc_ids),
                            KnowledgeDocument.deleted == 0,
                        )
                    )
                ).scalars().all()
            )
        )
    promote_to_golden(case, reviewer_id=user.id)
    await db.flush()
    return {
        "code": "0",
        "message": "success",
        "data": {
            "id": case.id,
            "caseType": case.case_type,
            "reviewStatus": case.review_status,
            "active": bool(case.active),
            "label": case_label(case.feedback_vote, case.review_status),
            "qualityScore": round(float(case.quality_score)),
        },
    }


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
    if request.kb_id and request.kb_id != "*":
        kb_statement = kb_statement.where(KnowledgeBase.id == request.kb_id)
    elif request.kb_id != "*":
        kb_statement = kb_statement.limit(1)
    kbs = list((await db.execute(kb_statement)).scalars().all())
    if not kbs:
        raise HTTPException(status_code=404, detail="没有可用于评测的知识库")

    scope_id = "*" if request.kb_id == "*" else kbs[0].id
    scope_name = "全部知识库" if scope_id == "*" else kbs[0].name
    scope_kb_ids = {kb.id for kb in kbs}

    cases = [
        case
        for case in await _all_cases(
            db,
            tenant_id=user.tenant_id or "default",
            kb_id=scope_id,
        )
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
                KnowledgeDocument.kb_id,
                KnowledgeDocument.content_hash,
                KnowledgeDocument.active_generation,
            ).where(
                KnowledgeDocument.kb_id.in_(scope_kb_ids),
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

    corpus_snapshot = _corpus_snapshot(corpus_rows)
    generation_by_doc = {
        row.id: row.active_generation or "" for row in corpus_rows
    }
    for case in cases:
        if case.knowledge_base_ids and not set(
            case.knowledge_base_ids
        ).issubset(scope_kb_ids):
            raise HTTPException(
                status_code=400,
                detail=f"case {case.id} targets knowledge bases outside the selected scope",
            )
        case_rows = [
            row
            for row in corpus_rows
            if not case.knowledge_base_ids
            or row.kb_id in case.knowledge_base_ids
        ]
        case_snapshot = _corpus_snapshot(case_rows)
        if (
            case.active
            and case.corpus_snapshot
            and case.corpus_snapshot != case_snapshot
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"case {case.id} targets corpus snapshot "
                    f"{case.corpus_snapshot}, selected corpus is "
                    f"{case_snapshot}"
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
        "kb_id": scope_id,
        "corpus_snapshot": corpus_snapshot,
        "index_generations": {
            kb.id: kb.active_index_generation for kb in kbs
        },
        "embedding_models": {kb.id: kb.embedding_model for kb in kbs},
        "prompt_version": "rag-safe-evidence-v0.0.5",
        "_runtime": {
            "user_id": user.id,
            "tenant_id": user.tenant_id or "default",
            "department_id": user.department_id or "",
            "role": user.role,
            "retrieval_scopes": [
                {
                    "kb_id": kb.id,
                    "kb_name": kb.name,
                    "collection_name": (
                        kb.active_collection_name or kb.collection_name
                    ),
                    "embedding_model": kb.embedding_model,
                }
                for kb in kbs
            ],
        },
    }
    if scope_id == "*" and request.graph_rag:
        config["graph_rag"] = True
    record = EvaluationRun(
        tenant_id=user.tenant_id or "default",
        kb_id=scope_id,
        kb_name=scope_name,
        dataset_version=_dataset_version(cases),
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
