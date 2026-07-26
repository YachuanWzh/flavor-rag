"""Evaluation console APIs: dataset inspection, live replay, and feedback mining."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database.session import get_db
from app.evaluation.runner import calculate_metrics, load_dataset, run_evaluation
from app.models import KnowledgeBase, Message, MessageFeedback, User
from app.rag.pipeline import RAGContext, RAGPipeline
from app.security.access import Permission
from app.security.service import kb_access_predicate, principal_from_user

router = APIRouter(prefix="/api/admin/evaluation", tags=["evaluation"])
_DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "minimal.jsonl"


class EvaluationRunRequest(BaseModel):
    kb_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    categories: list[str] = Field(default_factory=list)


@router.get("/overview")
async def overview(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cases = load_dataset(_DATASET)
    categories = Counter(case.category for case in cases if case.active)
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
    return {
        "code": "0",
        "data": {
            "caseCount": len(cases),
            "activeCaseCount": sum(case.active for case in cases),
            "categories": dict(categories),
            "negativeFeedbackCandidates": len(negative_count),
            "cases": [
                {
                    "id": case.id,
                    "question": case.question,
                    "category": case.category,
                    "answerable": case.answerable,
                    "active": case.active,
                    "expectedChunkIds": case.expected_chunk_ids,
                }
                for case in cases
            ],
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
        .where(
            MessageFeedback.deleted == 0,
            MessageFeedback.vote == -1,
        )
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
    pipeline = RAGPipeline()

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
                graph_rag=False,
            )
        )
        return {
            "chunk_ids": [source["chunkId"] for source in result.sources],
            "answerable": result.answerable,
            "leaked_chunk_ids": [],
        }

    results, metrics = await run_evaluation(cases, retrieve, top_k=request.top_k)
    by_category = {}
    for category in sorted({case.category for case in cases}):
        category_cases = [case for case in cases if case.category == category]
        ids = {case.id for case in category_cases}
        category_results = [result for result in results if result.case_id in ids]
        by_category[category] = calculate_metrics(
            category_cases,
            category_results,
            top_k=request.top_k,
        )
    return {
        "code": "0",
        "data": {
            "knowledgeBase": {"id": kb.id, "name": kb.name},
            "metrics": metrics,
            "byCategory": by_category,
            "results": [result.__dict__ for result in results],
        },
    }
