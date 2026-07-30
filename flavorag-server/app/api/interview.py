"""Private interview simulation API."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.chat import resolve_chat_kb_scopes
from app.auth.dependencies import get_admin_user, get_current_user
from app.config.settings import settings
from app.database.session import get_db
from app.ingestion.parser import DocumentParser
from app.ingestion.upload_validation import (
    UploadValidationError,
    save_upload_bounded,
)
from app.models import (
    InterviewAnswer,
    InterviewMaterial,
    InterviewProfile,
    InterviewQuestion,
    InterviewSession,
    KnowledgeChunk,
    KnowledgeDocument,
    User,
    UserProfile,
)
from app.rag.pipeline import RAGContext, RAGPipeline
from app.services.interview import (
    DEFAULT_QUESTIONS,
    DEFAULT_SCORE_DIMENSIONS,
    DIFFICULTIES,
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    ROLE_FIT_WEIGHTS,
    aggregate_interview_profile,
    build_fallback_questions,
    material_digest,
    refine_questions_with_agent,
    score_answers_with_agent,
)
from app.services.leetcode_hot100 import build_algorithm_questions


router = APIRouter(prefix="/api/interviews", tags=["interviews"])


class JDTextRequest(BaseModel):
    text: str = Field(min_length=20, max_length=100_000)
    title: str = Field(default="粘贴的岗位 JD", max_length=256)


class StartInterviewRequest(BaseModel):
    kb_id: str | None = None
    conversation_id: str | None = None
    target_role: str | None = Field(default=None, max_length=128)
    user_focus: str | None = Field(default=None, max_length=1000)
    difficulty: str = "senior"
    question_count: int = Field(default=DEFAULT_QUESTIONS, ge=MIN_QUESTIONS, le=MAX_QUESTIONS)
    algorithm_count: int = Field(default=0, ge=0, le=2)


class SaveAnswerRequest(BaseModel):
    answer: str = Field(default="", max_length=12_000)
    skipped: bool = False
    answer_language: str | None = Field(
        default=None,
        pattern=r"^(javascript|typescript|python)$",
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _material_summary(material: InterviewMaterial | None) -> dict:
    if not material:
        return {"uploaded": False}
    return {
        "uploaded": True,
        "fileName": material.file_name,
        "contentHash": material.content_hash,
        "fileSize": material.file_size,
        "updatedAt": str(material.updated_at) if material.updated_at else None,
    }


async def _user_materials(
    db: AsyncSession,
    user_id: str,
) -> dict[str, InterviewMaterial]:
    rows = (
        await db.execute(
            select(InterviewMaterial).where(InterviewMaterial.user_id == user_id)
        )
    ).scalars().all()
    return {row.kind: row for row in rows}


@router.get("/config")
async def get_interview_config(user: User = Depends(get_current_user)):
    return {
        "code": "0",
        "data": {
            "questionCount": {
                "min": MIN_QUESTIONS,
                "max": MAX_QUESTIONS,
                "default": DEFAULT_QUESTIONS,
            },
            "difficulties": [
                {"key": key, "label": label} for key, label in DIFFICULTIES.items()
            ],
            "scoreDimensions": DEFAULT_SCORE_DIMENSIONS,
            "roleFitWeights": ROLE_FIT_WEIGHTS,
        },
    }


@router.get("/materials")
async def get_materials(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    materials = await _user_materials(db, user.id)
    return {
        "code": "0",
        "data": {
            "resume": _material_summary(materials.get("RESUME")),
            "jd": _material_summary(materials.get("JD")),
        },
    }


@router.post("/materials/jd-text")
async def save_jd_text(
    request: JDTextRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = request.text.strip()
    digest = material_digest(content.encode("utf-8"))
    material, changed = await _upsert_material(
        db,
        user=user,
        kind="JD",
        file_name=request.title,
        mime_type="text/plain",
        file_size=len(content.encode("utf-8")),
        digest=digest,
        extracted_text=content,
    )
    return {
        "code": "0",
        "data": {**_material_summary(material), "changed": changed},
    }


@router.post("/materials/{kind}")
async def upload_material(
    kind: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    normalized_kind = kind.strip().upper()
    if normalized_kind not in {"RESUME", "JD"}:
        raise HTTPException(status_code=404, detail="材料类型不存在")
    extension = Path(file.filename or "").suffix.lower()
    allowed = {".pdf"} if normalized_kind == "RESUME" else {".pdf", ".docx", ".txt", ".md", ".markdown"}
    if extension not in allowed:
        expected = "PDF" if normalized_kind == "RESUME" else "PDF、DOCX、TXT 或 Markdown"
        raise HTTPException(status_code=400, detail=f"该材料仅支持 {expected}")

    temp_dir = tempfile.mkdtemp(prefix="flavorag-interview-")
    destination = os.path.join(temp_dir, f"material{extension}")
    try:
        try:
            size = await asyncio.to_thread(
                save_upload_bounded,
                file,
                destination,
                max_bytes=min(settings.upload_max_bytes, 10 * 1024 * 1024),
                max_pdf_pages=min(settings.upload_max_pdf_pages, 80),
                max_uncompressed_bytes=min(
                    settings.archive_max_uncompressed_bytes,
                    40 * 1024 * 1024,
                ),
                max_archive_entries=settings.archive_max_entries,
                max_compression_ratio=settings.archive_max_compression_ratio,
                max_image_pixels=settings.upload_max_image_pixels,
            )
        except UploadValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        raw = await asyncio.to_thread(Path(destination).read_bytes)
        digest = material_digest(raw)
        existing = (
            await db.execute(
                select(InterviewMaterial).where(
                    InterviewMaterial.user_id == user.id,
                    InterviewMaterial.kind == normalized_kind,
                )
            )
        ).scalar_one_or_none()
        if existing and existing.content_hash == digest:
            return {
                "code": "0",
                "data": {**_material_summary(existing), "changed": False},
            }

        extracted = (await DocumentParser().parse(destination)).strip()
        extracted = re.sub(r"\x00", "", extracted)[:100_000]
        if len(extracted) < 20:
            raise HTTPException(
                status_code=422,
                detail="未能从材料中提取足够文本，请上传文本型 PDF 或更清晰的文件",
            )
        material, changed = await _upsert_material(
            db,
            user=user,
            kind=normalized_kind,
            file_name=file.filename or f"{normalized_kind.lower()}{extension}",
            mime_type=file.content_type or "application/octet-stream",
            file_size=size,
            digest=digest,
            extracted_text=extracted,
        )
        return {
            "code": "0",
            "data": {**_material_summary(material), "changed": changed},
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _upsert_material(
    db: AsyncSession,
    *,
    user: User,
    kind: str,
    file_name: str,
    mime_type: str,
    file_size: int,
    digest: str,
    extracted_text: str,
) -> tuple[InterviewMaterial, bool]:
    material = (
        await db.execute(
            select(InterviewMaterial).where(
                InterviewMaterial.user_id == user.id,
                InterviewMaterial.kind == kind,
            )
        )
    ).scalar_one_or_none()
    if material and material.content_hash == digest:
        return material, False
    if material is None:
        material = InterviewMaterial(
            user_id=user.id,
            tenant_id=user.tenant_id or "default",
            kind=kind,
            content_hash=digest,
            extracted_text=extracted_text,
        )
        db.add(material)
    material.file_name = file_name
    material.mime_type = mime_type
    material.file_size = file_size
    material.content_hash = digest
    material.extracted_text = extracted_text
    material.updated_at = _utcnow()
    await db.flush()
    return material, True


@router.delete("/materials/{kind}")
async def delete_material(
    kind: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    normalized_kind = kind.strip().upper()
    material = (
        await db.execute(
            select(InterviewMaterial).where(
                InterviewMaterial.user_id == user.id,
                InterviewMaterial.kind == normalized_kind,
            )
        )
    ).scalar_one_or_none()
    if material:
        await db.delete(material)
    return {"code": "0", "data": {"deleted": bool(material)}}


@router.post("")
async def start_interview(
    request: StartInterviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if request.difficulty not in DIFFICULTIES:
        raise HTTPException(status_code=400, detail="不支持的面试难度")
    requested_scope = request.kb_id or "*"
    scopes = await resolve_chat_kb_scopes(db, user, requested_scope)
    if request.kb_id and not scopes:
        raise HTTPException(status_code=404, detail="知识库不存在或无权访问")
    materials = await _user_materials(db, user.id)
    resume = materials.get("RESUME")
    jd = materials.get("JD")
    resume_text = resume.extracted_text if resume else ""
    jd_text = jd.extracted_text if jd else ""
    target_role = (request.target_role or _infer_target_role(jd_text) or "目标岗位").strip()
    user_focus = (request.user_focus or "").strip()

    sources = await _retrieve_sources(
        db,
        user=user,
        scopes=scopes,
        selected_kb_id=request.kb_id,
        resume_text=resume_text,
        jd_text=jd_text,
        focus_text=user_focus,
        difficulty=request.difficulty,
    )
    scaffold = build_fallback_questions(
        sources=sources,
        count=request.question_count,
        resume_text=resume_text,
        jd_text=jd_text,
        focus_text=user_focus,
        difficulty=request.difficulty,
    )
    questions = await refine_questions_with_agent(
        scaffold,
        resume_text=resume_text,
        jd_text=jd_text,
        focus_text=user_focus,
        difficulty=request.difficulty,
    )
    questions.extend(build_algorithm_questions(request.algorithm_count))
    kb_name = (
        next((scope.kb_name for scope in scopes if scope.kb_id == request.kb_id), None)
        if request.kb_id
        else "全部可访问知识库"
    )
    interview = InterviewSession(
        user_id=user.id,
        tenant_id=user.tenant_id or "default",
        conversation_id=request.conversation_id,
        kb_id=request.kb_id,
        kb_name=kb_name,
        target_role=target_role,
        user_focus=user_focus or None,
        difficulty=request.difficulty,
        question_count=len(questions),
        resume_hash=resume.content_hash if resume else None,
        jd_hash=jd.content_hash if jd else None,
        status="IN_PROGRESS",
    )
    db.add(interview)
    await db.flush()
    for sequence, item in enumerate(questions, start=1):
        db.add(
            InterviewQuestion(
                interview_id=interview.id,
                sequence=sequence,
                category=item["category"],
                question=item["question"],
                follow_up=item.get("followUp"),
                rubric=item.get("rubric") or [],
                source=item.get("source"),
                metadata_json=item.get("metadata"),
                agent_generated=1 if item.get("agentGenerated") else 0,
            )
        )
    await db.flush()
    return {
        "code": "0",
        "data": await _interview_payload(db, interview, reveal_review=False),
    }


def _infer_target_role(jd_text: str) -> str:
    if not jd_text.strip():
        return ""
    first_lines = [
        re.sub(r"\s+", " ", line).strip(" #：:")
        for line in jd_text.splitlines()
        if line.strip()
    ]
    for line in first_lines[:8]:
        if any(token in line.lower() for token in ("工程师", "开发", "架构", "engineer", "developer", "岗位", "职位")):
            return line[:128]
    return first_lines[0][:128] if first_lines else ""


async def _retrieve_sources(
    db: AsyncSession,
    *,
    user: User,
    scopes: list,
    selected_kb_id: str | None,
    resume_text: str,
    jd_text: str,
    focus_text: str,
    difficulty: str,
) -> list[dict]:
    if not scopes:
        return []
    query = (
        f"{DIFFICULTIES.get(difficulty, '高级')}技术面试 核心原理 架构权衡 故障排查 "
        f"用户指定重点 {focus_text[:1000]} 候选人经历 {resume_text[:1800]} "
        f"岗位要求 {jd_text[:1400]}"
    )
    primary = scopes[0]
    try:
        async with asyncio.timeout(30):
            result = await RAGPipeline().run(
                RAGContext(
                    question=query[:3500],
                    kb_id=selected_kb_id,
                    collection_name=primary.collection_name if selected_kb_id else None,
                    embedding_model=primary.embedding_model if selected_kb_id else None,
                    user_id=user.id,
                    tenant_id=user.tenant_id or "default",
                    department_id=user.department_id or "",
                    role=user.role,
                    final_top_k=16,
                    retrieval_scopes=scopes,
                )
            )
        if result.sources:
            return result.sources[:16]
    except Exception:
        pass
    return await _relational_source_fallback(db, scopes, query)


async def _relational_source_fallback(
    db: AsyncSession,
    scopes: list,
    query: str,
) -> list[dict]:
    scope_ids = [scope.kb_id for scope in scopes]
    rows = (
        await db.execute(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.doc_id)
            .where(
                KnowledgeChunk.kb_id.in_(scope_ids),
                KnowledgeChunk.deleted == 0,
                KnowledgeChunk.enabled == 1,
                KnowledgeDocument.deleted == 0,
                KnowledgeDocument.enabled == 1,
            )
            .limit(400)
        )
    ).all()
    query_terms = {
        term.lower()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_+.#/-]{2,}|[\u4e00-\u9fff]{2,6}", query)
    }
    scope_names = {scope.kb_id: scope.kb_name for scope in scopes}
    ranked: list[tuple[float, KnowledgeChunk, KnowledgeDocument]] = []
    for chunk, document in rows:
        content = chunk.content or ""
        lowered = content.lower()
        overlap = sum(term in lowered for term in query_terms)
        score = overlap + min(1.0, len(content) / 1200)
        ranked.append((score, chunk, document))
    ranked.sort(key=lambda item: (item[0], -item[1].chunk_index), reverse=True)
    return [
        {
            "documentId": document.id,
            "chunkId": chunk.id,
            "docName": document.doc_name,
            "chunkIndex": chunk.chunk_index,
            "content": (chunk.content or "")[:800],
            "score": round(score, 4),
            "blockType": chunk.block_type,
            "pageStart": chunk.page_start,
            "pageEnd": chunk.page_end,
            "bboxes": chunk.bbox_json,
            "fileType": document.file_type,
            "kbId": chunk.kb_id,
            "kbName": scope_names.get(chunk.kb_id, ""),
        }
        for score, chunk, document in ranked[:16]
    ]


@router.get("/profile/me")
async def get_my_interview_profile(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {
        "code": "0",
        "data": await _profile_payload(db, user.id),
    }


@router.get("/admin/profiles/{user_id}")
async def get_admin_interview_profile(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    return {"code": "0", "data": await _profile_payload(db, user_id)}


async def _profile_payload(db: AsyncSession, user_id: str) -> dict:
    profile = (
        await db.execute(
            select(InterviewProfile).where(InterviewProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    recent = (
        await db.execute(
            select(InterviewSession)
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.status == "COMPLETED",
            )
            .order_by(InterviewSession.completed_at.desc())
            .limit(8)
        )
    ).scalars().all()
    return {
        "scoreDimensions": DEFAULT_SCORE_DIMENSIONS,
        "profile": (
            {
                "dimensionScores": profile.dimension_scores or {},
                "overallScore": profile.overall_score,
                "previousOverallScore": profile.previous_overall_score,
                "delta": profile.delta or 0,
                "trend": profile.trend or "stable",
                "interviewCount": profile.interview_count or 0,
                "latestInterviewId": profile.latest_interview_id,
                "targetRole": profile.target_role,
                "updatedAt": str(profile.updated_at) if profile.updated_at else None,
            }
            if profile
            else None
        ),
        "recent": [
            {
                "id": item.id,
                "targetRole": item.target_role,
                "kbName": item.kb_name,
                "difficulty": item.difficulty,
                "overallScore": item.overall_score,
                "completedAt": str(item.completed_at) if item.completed_at else None,
            }
            for item in recent
        ],
    }


@router.get("/history")
async def get_interview_history(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interviews = (
        await db.execute(
            select(InterviewSession)
            .where(
                InterviewSession.user_id == user.id,
                InterviewSession.tenant_id == (user.tenant_id or "default"),
                InterviewSession.status == "COMPLETED",
            )
            .order_by(
                InterviewSession.completed_at.desc(),
                InterviewSession.created_at.desc(),
            )
        )
    ).scalars().all()
    return {
        "code": "0",
        "data": {
            "total": len(interviews),
            "scoreDimensions": DEFAULT_SCORE_DIMENSIONS,
            "items": [
                {
                    "id": item.id,
                    "targetRole": item.target_role,
                    "kbName": item.kb_name,
                    "difficulty": item.difficulty,
                    "overallScore": item.overall_score,
                    "dimensionScores": item.dimension_scores or {},
                    "roleFitBreakdown": item.role_fit_breakdown or {},
                    "summary": item.summary,
                    "completedAt": (
                        str(item.completed_at) if item.completed_at else None
                    ),
                }
                for item in interviews
            ],
        },
    }


@router.delete("/history")
async def clear_interview_history(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    terminal_interviews = (
        await db.execute(
            select(InterviewSession.id).where(
                InterviewSession.user_id == user.id,
                InterviewSession.tenant_id == (user.tenant_id or "default"),
                InterviewSession.status.in_(("COMPLETED", "ABANDONED")),
            )
        )
    ).scalars().all()
    interview_ids = list(terminal_interviews)
    if interview_ids:
        await db.execute(
            delete(InterviewAnswer).where(
                InterviewAnswer.interview_id.in_(interview_ids)
            )
        )
        await db.execute(
            delete(InterviewQuestion).where(
                InterviewQuestion.interview_id.in_(interview_ids)
            )
        )
        await db.execute(
            delete(InterviewSession).where(
                InterviewSession.id.in_(interview_ids)
            )
        )
    await db.execute(
        delete(InterviewProfile).where(
            InterviewProfile.user_id == user.id,
            InterviewProfile.tenant_id == (user.tenant_id or "default"),
        )
    )
    await db.flush()
    return {
        "code": "0",
        "data": {
            "cleared": True,
            "deletedSessions": len(interview_ids),
        },
    }


@router.get("/{interview_id}")
async def get_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interview = await _owned_interview(db, interview_id, user)
    return {
        "code": "0",
        "data": await _interview_payload(
            db,
            interview,
            reveal_review=interview.status == "COMPLETED",
        ),
    }


@router.put("/{interview_id}/answers/{question_id}")
async def save_answer(
    interview_id: str,
    question_id: str,
    request: SaveAnswerRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interview = await _owned_interview(db, interview_id, user)
    if interview.status != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="当前面试已结束，不能修改答案")
    question = (
        await db.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.id == question_id,
                InterviewQuestion.interview_id == interview.id,
            )
        )
    ).scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="面试题不存在")
    answer = (
        await db.execute(
            select(InterviewAnswer).where(
                InterviewAnswer.interview_id == interview.id,
                InterviewAnswer.question_id == question.id,
            )
        )
    ).scalar_one_or_none()
    if answer is None:
        answer = InterviewAnswer(
            interview_id=interview.id,
            question_id=question.id,
            answer="",
        )
        db.add(answer)
    answer.answer = request.answer.strip()
    answer.answer_language = (
        request.answer_language
        if question.category == "algorithm"
        else None
    )
    answer.skipped = 1 if request.skipped else 0
    answer.answered_at = _utcnow()
    answer.updated_at = _utcnow()
    await db.flush()
    return {
        "code": "0",
        "data": {
            "questionId": question.id,
            "saved": True,
            "skipped": bool(answer.skipped),
            "answerLanguage": answer.answer_language,
        },
    }


@router.post("/{interview_id}/submit")
async def submit_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interview = await _owned_interview(db, interview_id, user)
    if interview.status == "COMPLETED":
        return {
            "code": "0",
            "data": await _interview_payload(db, interview, reveal_review=True),
        }
    if interview.status != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="当前面试不能提交评分")
    questions = (
        await db.execute(
            select(InterviewQuestion)
            .where(InterviewQuestion.interview_id == interview.id)
            .order_by(InterviewQuestion.sequence)
        )
    ).scalars().all()
    answer_rows = (
        await db.execute(
            select(InterviewAnswer).where(InterviewAnswer.interview_id == interview.id)
        )
    ).scalars().all()
    if len(answer_rows) != len(questions):
        raise HTTPException(status_code=409, detail="请先完成或跳过全部题目")
    answers = {
        row.question_id: {
            "answer": row.answer,
            "answerLanguage": row.answer_language,
            "skipped": bool(row.skipped),
        }
        for row in answer_rows
    }
    question_data = [
        {
            "id": question.id,
            "category": question.category,
            "question": question.question,
            "rubric": question.rubric or [],
            "source": question.source,
            "metadata": question.metadata_json,
        }
        for question in questions
    ]
    interview.status = "SCORING"
    await db.flush()
    result = await score_answers_with_agent(
        question_data,
        answers,
        has_resume=bool(interview.resume_hash),
        has_jd=bool(interview.jd_hash),
        target_role=interview.target_role or "目标岗位",
        difficulty=interview.difficulty,
    )
    review_map = {item["questionId"]: item for item in result["reviews"]}
    for answer in answer_rows:
        review = review_map[answer.question_id]
        answer.score = review["score"]
        answer.analysis = review["analysis"]
        answer.strengths = review["strengths"]
        answer.improvements = review["improvements"]
        answer.reference_points = review["referencePoints"]

    interview.status = "COMPLETED"
    interview.overall_score = result["overallScore"]
    interview.dimension_scores = result["dimensionScores"]
    interview.role_fit_breakdown = result["roleFitBreakdown"]
    interview.summary = result["summary"]
    interview.completed_at = _utcnow()
    interview.updated_at = _utcnow()
    await _update_profile(db, interview)
    await db.flush()
    return {
        "code": "0",
        "data": await _interview_payload(db, interview, reveal_review=True),
    }


@router.post("/{interview_id}/abandon")
async def abandon_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    interview = await _owned_interview(db, interview_id, user)
    if interview.status == "COMPLETED":
        raise HTTPException(status_code=409, detail="已完成的面试不能退出")
    if interview.status == "SCORING":
        raise HTTPException(status_code=409, detail="面试正在评分，不能退出")
    if interview.status != "ABANDONED":
        interview.status = "ABANDONED"
        interview.completed_at = _utcnow()
        interview.updated_at = _utcnow()
        await db.flush()
    return {
        "code": "0",
        "data": await _interview_payload(db, interview, reveal_review=False),
    }


async def _update_profile(db: AsyncSession, interview: InterviewSession) -> None:
    base_profile = (
        await db.execute(
            select(UserProfile).where(UserProfile.user_id == interview.user_id)
        )
    ).scalar_one_or_none()
    if base_profile is None:
        db.add(
            UserProfile(
                user_id=interview.user_id,
                tenant_id=interview.tenant_id,
                last_active_time=_utcnow(),
            )
        )
    else:
        base_profile.last_active_time = _utcnow()

    profile = (
        await db.execute(
            select(InterviewProfile).where(
                InterviewProfile.user_id == interview.user_id
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = InterviewProfile(
            user_id=interview.user_id,
            tenant_id=interview.tenant_id,
            interview_count=0,
        )
        db.add(profile)
    aggregated = aggregate_interview_profile(
        profile.dimension_scores or {},
        interview.dimension_scores or {},
        interview_count=profile.interview_count or 0,
    )
    profile.previous_overall_score = aggregated["previousOverallScore"]
    profile.dimension_scores = aggregated["scores"]
    profile.overall_score = aggregated["overallScore"]
    profile.delta = aggregated["delta"]
    profile.trend = aggregated["trend"]
    profile.interview_count = (profile.interview_count or 0) + 1
    profile.latest_interview_id = interview.id
    profile.target_role = interview.target_role
    profile.updated_at = _utcnow()


async def _owned_interview(
    db: AsyncSession,
    interview_id: str,
    user: User,
) -> InterviewSession:
    interview = (
        await db.execute(
            select(InterviewSession).where(
                InterviewSession.id == interview_id,
                InterviewSession.user_id == user.id,
                InterviewSession.tenant_id == (user.tenant_id or "default"),
            )
        )
    ).scalar_one_or_none()
    if not interview:
        raise HTTPException(status_code=404, detail="面试记录不存在")
    return interview


async def _interview_payload(
    db: AsyncSession,
    interview: InterviewSession,
    *,
    reveal_review: bool,
) -> dict:
    questions = (
        await db.execute(
            select(InterviewQuestion)
            .where(InterviewQuestion.interview_id == interview.id)
            .order_by(InterviewQuestion.sequence)
        )
    ).scalars().all()
    answers = {
        answer.question_id: answer
        for answer in (
            await db.execute(
                select(InterviewAnswer).where(
                    InterviewAnswer.interview_id == interview.id
                )
            )
        ).scalars().all()
    }
    payload = {
        "id": interview.id,
        "status": interview.status,
        "kbId": interview.kb_id,
        "kbName": interview.kb_name,
        "targetRole": interview.target_role,
        "userFocus": interview.user_focus,
        "difficulty": interview.difficulty,
        "questionCount": interview.question_count,
        "hasResume": bool(interview.resume_hash),
        "hasJd": bool(interview.jd_hash),
        "startedAt": str(interview.started_at) if interview.started_at else None,
        "completedAt": str(interview.completed_at) if interview.completed_at else None,
        "questions": [],
    }
    for question in questions:
        answer = answers.get(question.id)
        item = {
            "id": question.id,
            "sequence": question.sequence,
            "category": question.category,
            "question": question.question,
            "hasSource": bool(question.source),
            "agentGenerated": bool(question.agent_generated),
            "algorithm": question.metadata_json if question.category == "algorithm" else None,
            "answer": answer.answer if answer else "",
            "answerLanguage": answer.answer_language if answer else None,
            "skipped": bool(answer.skipped) if answer else False,
            "answered": answer is not None,
        }
        if reveal_review:
            item.update(
                {
                    "followUp": question.follow_up,
                    "rubric": question.rubric or [],
                    "source": question.source,
                    "score": answer.score if answer else 0,
                    "analysis": answer.analysis if answer else "",
                    "strengths": answer.strengths if answer else [],
                    "improvements": answer.improvements if answer else [],
                    "referencePoints": answer.reference_points if answer else [],
                }
            )
        payload["questions"].append(item)
    if reveal_review:
        profile = (
            await db.execute(
                select(InterviewProfile).where(
                    InterviewProfile.user_id == interview.user_id
                )
            )
        ).scalar_one_or_none()
        payload.update(
            {
                "overallScore": interview.overall_score,
                "dimensionScores": interview.dimension_scores or {},
                "scoreDimensions": DEFAULT_SCORE_DIMENSIONS,
                "roleFitBreakdown": interview.role_fit_breakdown or {},
                "summary": interview.summary,
                "profileDelta": profile.delta if profile else 0,
                "profileTrend": profile.trend if profile else "stable",
            }
        )
    return payload
