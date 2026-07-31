"""Production-question assets and their promotion into evaluation cases."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.runner import EvaluationCase
from app.models import EvaluationDatasetCase, Message, MessageFeedback, gen_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _source_ids(sources: list[dict] | None, key: str) -> list[str]:
    return list(
        dict.fromkeys(
            str(source.get(key)).strip()
            for source in (sources or [])
            if source.get(key)
        )
    )


def calculate_quality_score(
    answer: Message | None,
    vote: int | None = None,
) -> int:
    """Return an explainable 0-100 triage score, not an LLM quality verdict."""
    if vote == -1:
        return 15
    if vote == 1:
        return 95
    if answer is None or not answer.content.strip():
        return 20
    score = 55
    if answer.sources:
        score += 15
    if len(answer.content.strip()) >= 80:
        score += 10
    if answer.message_status == "INTERRUPTED":
        score -= 30
    return max(0, min(100, score))


def case_label(vote: int | None, review_status: str | None = None) -> str:
    if vote == -1:
        return "BAD_CASE"
    if review_status == "approved":
        return "GOLDEN"
    if vote == 1:
        return "GOOD_CASE"
    return "UNRATED"


async def ensure_base_case(
    db: AsyncSession,
    *,
    question: Message,
    answer: Message,
    feedback: MessageFeedback | None = None,
) -> EvaluationDatasetCase:
    """Create or refresh the one automatic base case for a question."""
    existing = (
        await db.execute(
            select(EvaluationDatasetCase).where(
                EvaluationDatasetCase.tenant_id == question.tenant_id,
                EvaluationDatasetCase.source_question_id == question.id,
                EvaluationDatasetCase.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    sources = answer.sources or []
    chunk_ids = _source_ids(sources, "chunkId")
    doc_ids = _source_ids(sources, "documentId")
    kb_ids = _source_ids(sources, "kbId")
    vote = feedback.vote if feedback else None
    tags = ["production", "base_case"]
    if vote == -1:
        tags.extend(["bad_case", "needs_review"])

    if existing is None:
        existing = EvaluationDatasetCase(
            id=gen_id(),
            tenant_id=question.tenant_id or "default",
            source_question_id=question.id,
            source_answer_id=answer.id,
            user_id=question.user_id,
            conversation_id=question.conversation_id,
            case_type="base",
            review_status="generated",
            question=question.content,
        )
        db.add(existing)

    existing.source_answer_id = answer.id
    existing.question = question.content
    existing.expected_answer = answer.content
    existing.retrieved_chunk_ids = chunk_ids
    existing.retrieved_doc_ids = doc_ids
    existing.knowledge_base_ids = kb_ids
    existing.tags = tags
    existing.answerable = 1 if answer.content.strip() else 0
    existing.quality_score = calculate_quality_score(answer, vote)
    existing.feedback_vote = vote
    existing.feedback_reason = feedback.reason if feedback else None
    existing.feedback_comment = feedback.comment if feedback else None
    if existing.case_type != "golden":
        existing.active = 0
        existing.expected_chunk_ids = []
        existing.expected_doc_ids = []
    return existing


async def sync_case_feedback(
    db: AsyncSession,
    *,
    answer: Message,
    feedback: MessageFeedback,
) -> None:
    case = (
        await db.execute(
            select(EvaluationDatasetCase).where(
                EvaluationDatasetCase.source_answer_id == answer.id,
                EvaluationDatasetCase.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if case is None:
        return
    case.feedback_vote = feedback.vote
    case.feedback_reason = feedback.reason
    case.feedback_comment = feedback.comment
    case.quality_score = calculate_quality_score(answer, feedback.vote)
    tags = [tag for tag in (case.tags or []) if tag not in {"bad_case", "needs_review"}]
    if feedback.vote == -1:
        tags.extend(["bad_case", "needs_review"])
        case.active = 0
        case.review_status = "needs_review"
        # Retrieved evidence from a bad answer is diagnostic material, never
        # ground truth. Preserve it in retrieved_* while clearing expected_*.
        case.expected_chunk_ids = []
        case.expected_doc_ids = []
    case.tags = list(dict.fromkeys(tags))


def promote_to_golden(
    case: EvaluationDatasetCase,
    *,
    reviewer_id: str,
) -> EvaluationDatasetCase:
    """Promote a base case. Bad/no-evidence cases are saved but fail closed."""
    case.case_type = "golden"
    case.promoted_by = reviewer_id
    case.promoted_at = _utcnow()
    tags = [tag for tag in (case.tags or []) if tag != "base_case"]
    tags.append("golden_dataset")
    if case.feedback_vote == -1:
        case.review_status = "needs_review"
        case.active = 0
        case.expected_chunk_ids = []
        case.expected_doc_ids = []
        tags.extend(["bad_case", "needs_review"])
    elif (
        (not case.retrieved_chunk_ids and not case.retrieved_doc_ids)
        or not case.knowledge_base_ids
    ):
        case.review_status = "needs_review"
        case.active = 0
        tags.append(
            "missing_corpus_scope"
            if not case.knowledge_base_ids
            else "missing_ground_truth"
        )
    else:
        case.review_status = "approved"
        case.active = 1
        case.expected_chunk_ids = list(case.retrieved_chunk_ids or [])
        case.expected_doc_ids = list(case.retrieved_doc_ids or [])
    case.tags = list(dict.fromkeys(tags))
    return case


def to_evaluation_case(case: EvaluationDatasetCase) -> EvaluationCase:
    return EvaluationCase(
        id=f"production-{case.id}",
        question=case.question,
        expected_chunk_ids=list(case.expected_chunk_ids or []),
        expected_doc_ids=list(case.expected_doc_ids or []),
        category=case.category,
        answerable=bool(case.answerable),
        knowledge_base_ids=list(case.knowledge_base_ids or []),
        active=bool(case.active),
        difficulty=case.difficulty,
        tags=list(case.tags or []),
        expected_answer=case.expected_answer or "",
    )
