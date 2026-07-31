from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.evaluation import list_question_assets
from app.evaluation.cases import promote_to_golden, sync_case_feedback
from app.models import (
    Base,
    EvaluationDatasetCase,
    Message,
    MessageFeedback,
    User,
    gen_id,
)
from app.services.chat_service import ChatService


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_completed_answer_automatically_creates_base_case():
    engine, factory = await _session()
    try:
        async with factory() as db:
            user = User(
                id="user-1",
                username="alice",
                password="hashed",
                tenant_id="tenant-1",
            )
            db.add(user)
            service = ChatService(db, user_id=user.id, tenant_id=user.tenant_id)
            question_id = await service.save_message("conv-1", "user", "如何配置检索？")
            answer_id = await service.save_message(
                "conv-1",
                "assistant",
                "请先创建知识库，再设置检索参数。" * 5,
                sources=[
                    {"chunkId": "chunk-1", "documentId": "doc-1", "kbId": "kb-1"}
                ],
            )
            case = (
                await db.execute(select(EvaluationDatasetCase))
            ).scalar_one()

            assert case.source_question_id == question_id
            assert case.source_answer_id == answer_id
            assert case.case_type == "base"
            assert case.active == 0
            assert case.retrieved_chunk_ids == ["chunk-1"]
            assert case.expected_chunk_ids == []
            assert case.quality_score == 80
            promote_to_golden(case, reviewer_id="reviewer-1")
            assert case.review_status == "approved"
            assert case.active == 1
            assert case.expected_chunk_ids == ["chunk-1"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bad_case_is_scored_labeled_and_fails_closed_on_promotion():
    engine, factory = await _session()
    try:
        async with factory() as db:
            user = User(
                id="user-1",
                username="alice",
                password="hashed",
                tenant_id="tenant-1",
            )
            db.add(user)
            service = ChatService(db, user_id=user.id, tenant_id=user.tenant_id)
            await service.save_message("conv-1", "user", "错误答案测试")
            answer_id = await service.save_message(
                "conv-1",
                "assistant",
                "这是一个有召回来源但被用户点踩的答案。" * 5,
                sources=[{"chunkId": "wrong-chunk", "documentId": "wrong-doc"}],
            )
            answer = await db.get(Message, answer_id)
            feedback = MessageFeedback(
                id=gen_id(),
                message_id=answer_id,
                conversation_id="conv-1",
                user_id=user.id,
                vote=-1,
                reason="答案错误",
            )
            db.add(feedback)
            await sync_case_feedback(db, answer=answer, feedback=feedback)
            case = (
                await db.execute(select(EvaluationDatasetCase))
            ).scalar_one()
            promote_to_golden(case, reviewer_id="reviewer-1")

            assert case.case_type == "golden"
            assert case.review_status == "needs_review"
            assert case.active == 0
            assert case.quality_score == 15
            assert "bad_case" in case.tags
            assert case.retrieved_chunk_ids == ["wrong-chunk"]
            assert case.expected_chunk_ids == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_question_asset_list_separates_logged_in_users():
    engine, factory = await _session()
    try:
        async with factory() as db:
            alice = User(
                id="user-1",
                username="alice",
                password="hashed",
                tenant_id="tenant-1",
            )
            bob = User(
                id="user-2",
                username="bob",
                password="hashed",
                tenant_id="tenant-1",
            )
            db.add_all([alice, bob])
            await ChatService(db, user_id=alice.id, tenant_id=alice.tenant_id).save_message(
                "conv-a", "user", "Alice 的问题"
            )
            await ChatService(db, user_id=bob.id, tenant_id=bob.tenant_id).save_message(
                "conv-b", "user", "Bob 的问题"
            )
            payload = await list_question_assets(
                q=None,
                user_id=None,
                label=None,
                page=1,
                page_size=20,
                db=db,
                user=alice,
            )

            assert payload["data"]["total"] == 1
            assert payload["data"]["items"][0]["user"]["username"] == "alice"
    finally:
        await engine.dispose()
