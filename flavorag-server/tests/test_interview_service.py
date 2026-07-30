from app.services.interview import (
    DEFAULT_SCORE_DIMENSIONS,
    aggregate_interview_profile,
    allocate_question_quota,
    build_fallback_questions,
    extract_resume_signals,
    material_digest,
    score_interview_answers,
)
from app.services.leetcode_hot100 import (
    DIFFICULTY_WEIGHTS,
    HOT_100_RUNNABLE_QUESTIONS,
    build_algorithm_questions,
)
import random
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.dependencies import get_current_user
from app.database.session import get_db
from app.main import app
from app.models import Base, KnowledgeBase, User


def _sources() -> list[dict]:
    return [
        {
            "documentId": f"doc-{index}",
            "chunkId": f"chunk-{index}",
            "docName": f"Architecture {index}.pdf",
            "chunkIndex": index,
            "content": f"Distributed system concept {index} with trade-offs and failure modes.",
            "score": 0.9 - index * 0.01,
            "pageStart": index + 1,
            "pageEnd": index + 1,
            "kbId": "kb-specialized",
            "kbName": "Distributed Systems",
        }
        for index in range(8)
    ]


def test_material_digest_detects_updates_without_filename_dependency():
    original = material_digest(b"same resume bytes")
    assert original == material_digest(b"same resume bytes")
    assert original != material_digest(b"updated resume bytes")
    assert len(original) == 64


def test_question_quota_preserves_required_mix():
    assert allocate_question_quota(12, has_resume=True, has_jd=True) == {
        "knowledge": 7,
        "profile": 3,
        "scenario": 2,
    }
    assert allocate_question_quota(10, has_resume=True, has_jd=False) == {
        "knowledge": 7,
        "profile": 3,
        "scenario": 0,
    }
    assert allocate_question_quota(12, has_resume=False, has_jd=False) == {
        "knowledge": 7,
        "profile": 0,
        "scenario": 5,
    }


def test_fallback_question_generation_is_source_grounded_and_big_tech_style():
    questions = build_fallback_questions(
        sources=_sources(),
        count=12,
        resume_text="Built a high-throughput order service.",
        jd_text="Own distributed systems reliability and capacity planning.",
        difficulty="senior",
    )

    assert len(questions) == 12
    assert sum(q["category"] == "knowledge" for q in questions) == 7
    assert sum(q["category"] == "profile" for q in questions) == 3
    assert sum(q["category"] == "scenario" for q in questions) == 2
    assert all(q["source"] for q in questions)
    assert all(q["rubric"] for q in questions)
    assert all(q["followUp"] for q in questions)


def test_resume_signals_and_user_focus_anchor_most_questions():
    resume = """
    OrderGuard 智能订单项目
    负责设计 Agent 任务规划与工具调用链路，使用 RAG 召回故障手册。
    将检索命中率从 68% 提升到 87%，线上诊断延迟降低 35%。
    Python / FastAPI / PostgreSQL
    """
    signals = extract_resume_signals(resume)
    assert any("OrderGuard" in signal for signal in signals)
    assert any("87%" in signal or "35%" in signal for signal in signals)

    questions = build_fallback_questions(
        sources=_sources(),
        count=12,
        resume_text=resume,
        jd_text="负责企业级 Agent 平台、RAG 检索评估与线上质量保障。",
        focus_text="重点考 Agent 规划、RAG 检索评估和简历项目深挖",
        difficulty="senior",
    )

    resume_anchored = sum(
        any(token in question["question"] for token in ("OrderGuard", "87%", "35%"))
        for question in questions
    )
    focus_anchored = sum(
        "Agent" in question["question"] or "RAG" in question["question"]
        for question in questions
    )
    assert resume_anchored >= 9
    assert focus_anchored >= 9
    assert all(question["source"] for question in questions)


def test_hot100_questions_are_appended_runnable_and_weighted_to_easy_mid():
    questions = build_algorithm_questions(2, rng=random.Random(7))

    assert len(questions) == 2
    assert all(question["category"] == "algorithm" for question in questions)
    assert [question["question"].splitlines()[0] for question in questions] == [
        f"算法题{index}-{question['metadata']['title']}-{question['metadata']['difficulty']}"
        for index, question in enumerate(questions, start=1)
    ]
    assert len({question["metadata"]["slug"] for question in questions}) == 2
    assert all(question["metadata"]["description"] for question in questions)
    assert all(question["metadata"]["parameters"] for question in questions)
    assert all(question["metadata"]["constraints"] for question in questions)
    assert all(question["metadata"]["starterCode"] for question in questions)
    assert all(
        set(question["metadata"]["starterCodes"])
        == {"javascript", "typescript", "python"}
        for question in questions
    )
    assert all(question["metadata"]["testCases"] for question in questions)
    assert sum(DIFFICULTY_WEIGHTS.values()) == pytest.approx(1.0)
    assert DIFFICULTY_WEIGHTS == {"easy": 0.35, "mid": 0.60, "hard": 0.05}
    assert DIFFICULTY_WEIGHTS["hard"] < DIFFICULTY_WEIGHTS["easy"]
    assert DIFFICULTY_WEIGHTS["hard"] < DIFFICULTY_WEIGHTS["mid"]
    assert len(HOT_100_RUNNABLE_QUESTIONS) >= 20
    assert all(question["description"] for question in HOT_100_RUNNABLE_QUESTIONS)
    assert all(question["parameters"] for question in HOT_100_RUNNABLE_QUESTIONS)
    assert all(question["constraints"] for question in HOT_100_RUNNABLE_QUESTIONS)


def test_profile_uses_smoothed_scores_and_reports_direction():
    previous = {
        "knowledgeAccuracy": 6.0,
        "technicalDepth": 6.0,
        "practicalApplication": 6.0,
        "problemSolving": 6.0,
        "communication": 6.0,
        "roleFit": 6.0,
    }
    current = {dimension["key"]: 8.0 for dimension in DEFAULT_SCORE_DIMENSIONS}

    updated = aggregate_interview_profile(previous, current, interview_count=4)

    assert updated["scores"]["knowledgeAccuracy"] == 6.7
    assert updated["overallScore"] == 6.7
    assert updated["delta"] == 0.7
    assert updated["trend"] == "up"


@pytest.mark.asyncio
async def test_interview_api_runs_material_generation_answer_and_profile_flow(
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        user = User(
            id="user-interview",
            username="candidate",
            password="hashed",
            role="admin",
            tenant_id="default",
        )
        session.add(user)
        session.add(
            KnowledgeBase(
                id="kb-specialized",
                name="Distributed Systems",
                embedding_model="test",
                collection_name="test_interview",
                tenant_id="default",
                created_by=user.id,
            )
        )
        await session.commit()

    async def override_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_user():
        return user

    async def fake_retrieve(*args, **kwargs):
        return _sources()

    async def keep_scaffold(scaffold, **kwargs):
        return scaffold

    async def local_score(questions, answers, **kwargs):
        return score_interview_answers(
            questions,
            answers,
            has_resume=kwargs["has_resume"],
            has_jd=kwargs["has_jd"],
        )

    import app.api.interview as interview_api

    monkeypatch.setattr(interview_api, "_retrieve_sources", fake_retrieve)
    monkeypatch.setattr(interview_api, "refine_questions_with_agent", keep_scaffold)
    monkeypatch.setattr(interview_api, "score_answers_with_agent", local_score)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            jd = "高级后端工程师，负责分布式系统可靠性、容量规划、故障演练与架构演进。"
            first_material = await client.post(
                "/api/interviews/materials/jd-text",
                json={"text": jd, "title": "backend-jd"},
            )
            second_material = await client.post(
                "/api/interviews/materials/jd-text",
                json={"text": jd, "title": "renamed-jd"},
            )
            assert first_material.json()["data"]["changed"] is True
            assert second_material.json()["data"]["changed"] is False

            started = await client.post(
                "/api/interviews",
                json={
                    "kb_id": "kb-specialized",
                    "target_role": "高级后端工程师",
                    "user_focus": "重点考 Agent 规划和 RAG 评估",
                    "difficulty": "senior",
                    "question_count": 10,
                    "algorithm_count": 2,
                },
            )
            assert started.status_code == 200
            interview = started.json()["data"]
            assert interview["userFocus"] == "重点考 Agent 规划和 RAG 评估"
            assert len(interview["questions"]) == 12
            regular_questions = interview["questions"][:-2]
            algorithm_questions = interview["questions"][-2:]
            assert all(question["hasSource"] for question in regular_questions)
            assert all(
                question["category"] == "algorithm"
                and question["algorithm"]["starterCode"]
                and question["algorithm"]["description"]
                and question["algorithm"]["constraints"]
                and question["algorithm"]["testCases"]
                and question["question"].startswith(f"算法题{index}-")
                for index, question in enumerate(algorithm_questions, start=1)
            )
            assert all("rubric" not in question for question in interview["questions"])

            for question in interview["questions"]:
                answer = (
                    question["algorithm"]["starterCodes"]["typescript"]
                    if question["category"] == "algorithm"
                    else (
                        "首先明确目标和约束，其次分析一致性、容量与故障风险，"
                        "最后通过指标、压测和回滚演练验证方案取舍。"
                    )
                )
                response = await client.put(
                    f"/api/interviews/{interview['id']}/answers/{question['id']}",
                    json={
                        "answer": answer,
                        "skipped": False,
                        "answer_language": (
                            "typescript"
                            if question["category"] == "algorithm"
                            else None
                        ),
                    },
                )
                assert response.status_code == 200

            completed = await client.post(
                f"/api/interviews/{interview['id']}/submit"
            )
            report = completed.json()["data"]
            assert report["status"] == "COMPLETED"
            assert 0 <= report["overallScore"] <= 10
            assert len(report["dimensionScores"]) == 6
            assert all(question["source"] for question in report["questions"][:-2])
            assert all(
                question["category"] == "algorithm"
                and question["answerLanguage"] == "typescript"
                and question["referencePoints"]
                for question in report["questions"][-2:]
            )

            profile = await client.get("/api/interviews/profile/me")
            assert profile.json()["data"]["profile"]["interviewCount"] == 1

            abandoned_started = await client.post(
                "/api/interviews",
                json={
                    "kb_id": "kb-specialized",
                    "target_role": "高级后端工程师",
                    "difficulty": "senior",
                    "question_count": 10,
                },
            )
            abandoned = abandoned_started.json()["data"]
            exited = await client.post(
                f"/api/interviews/{abandoned['id']}/abandon"
            )
            assert exited.status_code == 200
            assert exited.json()["data"]["status"] == "ABANDONED"

            blocked_save = await client.put(
                f"/api/interviews/{abandoned['id']}/answers/{abandoned['questions'][0]['id']}",
                json={"answer": "退出后不应允许继续保存", "skipped": False},
            )
            assert blocked_save.status_code == 409
            blocked_submit = await client.post(
                f"/api/interviews/{abandoned['id']}/submit"
            )
            assert blocked_submit.status_code == 409

            unchanged_profile = await client.get("/api/interviews/profile/me")
            assert (
                unchanged_profile.json()["data"]["profile"]["interviewCount"]
                == 1
            )

            history = await client.get("/api/interviews/history")
            assert history.status_code == 200
            history_data = history.json()["data"]
            assert history_data["total"] == 1
            assert history_data["items"][0]["id"] == interview["id"]
            assert len(history_data["items"][0]["dimensionScores"]) == 6
            assert history_data["items"][0]["overallScore"] == report["overallScore"]

            active_started = await client.post(
                "/api/interviews",
                json={
                    "kb_id": "kb-specialized",
                    "target_role": "高级后端工程师",
                    "difficulty": "senior",
                    "question_count": 10,
                },
            )
            active_interview = active_started.json()["data"]

            cleared_history = await client.delete("/api/interviews/history")
            assert cleared_history.status_code == 200
            assert cleared_history.json()["data"]["deletedSessions"] == 2

            empty_history = await client.get("/api/interviews/history")
            assert empty_history.json()["data"]["total"] == 0
            reset_profile = await client.get("/api/interviews/profile/me")
            assert reset_profile.json()["data"]["profile"] is None
            assert reset_profile.json()["data"]["recent"] == []
            assert (
                await client.get(f"/api/interviews/{interview['id']}")
            ).status_code == 404
            assert (
                await client.get(f"/api/interviews/{abandoned['id']}")
            ).status_code == 404
            assert (
                await client.get(f"/api/interviews/{active_interview['id']}")
            ).status_code == 200

            cleared_jd = await client.delete("/api/interviews/materials/jd")
            assert cleared_jd.status_code == 200
            assert cleared_jd.json()["data"]["deleted"] is True
            materials_after_clear = await client.get("/api/interviews/materials")
            assert materials_after_clear.json()["data"]["jd"]["uploaded"] is False

            profile_list = await client.get("/api/admin/profiles")
            assert profile_list.json()["data"]["items"][0]["userId"] == user.id
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
