"""Generate bounded follow-up questions from configured samples and evidence."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models import SampleQuestion


async def recommend_questions(
    db: AsyncSession,
    *,
    question: str,
    kb_id: str | None,
    tenant_id: str,
    sources: list[dict],
) -> list[str]:
    if not settings.recommended_questions_enabled:
        return []
    limit = max(1, settings.recommended_questions_count)
    rows = await db.execute(
        select(SampleQuestion)
        .where(
            SampleQuestion.deleted == 0,
            SampleQuestion.enabled == 1,
            SampleQuestion.tenant_id == tenant_id,
            or_(
                SampleQuestion.kb_id.is_(None),
                SampleQuestion.kb_id == "",
                *([SampleQuestion.kb_id == kb_id] if kb_id else []),
            ),
        )
        .order_by(SampleQuestion.sort_order, SampleQuestion.create_time)
        .limit(limit * 3)
    )
    recommendations: list[str] = []
    for item in rows.scalars().all():
        candidate = item.question.strip()
        if candidate and candidate != question.strip() and candidate not in recommendations:
            recommendations.append(candidate)
            if len(recommendations) >= limit:
                return recommendations

    doc_names = []
    for source in sources:
        name = str(source.get("docName") or "").strip()
        if name and name != "unknown" and name not in doc_names:
            doc_names.append(name)
    fallbacks = [
        f"{name} 中还有哪些关键注意事项？" for name in doc_names[:2]
    ]
    fallbacks.append("能把刚才的答案整理成操作步骤吗？")
    for candidate in fallbacks:
        if candidate not in recommendations:
            recommendations.append(candidate)
        if len(recommendations) >= limit:
            break
    return recommendations
