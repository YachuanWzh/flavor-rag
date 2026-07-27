"""Outbox-based async ingestion: enqueue + worker claim/retry/DEAD state machine."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ingestion.chunker import ChunkConfig
from app.models import Base, IngestionJob, KnowledgeBase, KnowledgeDocument
from app.services.ingestion_jobs import IngestionJobWorker, enqueue_ingestion_job


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _make_env(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    source = tmp_path / "doc.md"
    source.write_text("# hello", encoding="utf-8")

    async with sessions() as session:
        kb = KnowledgeBase(
            id="kb-1",
            name="kb",
            embedding_model="mock",
            collection_name="c_kb1",
            created_by="user-1",
        )
        doc = KnowledgeDocument(
            id="doc-1",
            kb_id="kb-1",
            doc_name="doc.md",
            file_url=str(source),
            file_type="md",
            status="running",
            created_by="user-1",
        )
        session.add_all([kb, doc])
        job = await enqueue_ingestion_job(
            session,
            kb=kb,
            doc=doc,
            file_path=str(source),
            source_type="file",
            chunk_config=ChunkConfig(strategy="FIXED_WINDOW", chunk_size=512, overlap=128),
            user_id="user-1",
            tenant_id="default",
        )
        await session.commit()
        job_id = job.id
    return sessions, job_id, str(source)


@pytest.mark.asyncio
async def test_enqueue_creates_queued_job_and_marks_doc(tmp_path):
    sessions, job_id, source = await _make_env(tmp_path)
    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        doc = (
            await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == "doc-1")
            )
        ).scalar_one()
        assert job.status == "QUEUED"
        assert job.attempts == 0
        assert job.file_path == source
        assert job.chunk_config_json == {"chunkSize": 512, "overlapSize": 128}
        assert doc.status == "queued"


@pytest.mark.asyncio
async def test_worker_executes_job_to_success(tmp_path, monkeypatch):
    sessions, job_id, _ = await _make_env(tmp_path)

    async def fake_execute(db, *, kb, doc, **kwargs):
        doc.status = "success"
        doc.chunk_count = 7
        return 7

    monkeypatch.setattr(
        "app.services.ingestion_executor.execute_ingestion", fake_execute
    )

    processed = await IngestionJobWorker(concurrency=2).run_once(sessions)
    assert processed == 1

    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        doc = (
            await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == "doc-1")
            )
        ).scalar_one()
        assert job.status == "SUCCESS"
        assert job.attempts == 1
        assert job.chunk_count == 7
        assert job.duration_ms is not None
        assert doc.status == "success"
        assert doc.chunk_count == 7


@pytest.mark.asyncio
async def test_worker_retries_then_dead(tmp_path, monkeypatch):
    sessions, job_id, _ = await _make_env(tmp_path)

    async def failing_execute(db, **kwargs):
        raise RuntimeError("milvus down")

    monkeypatch.setattr(
        "app.services.ingestion_executor.execute_ingestion", failing_execute
    )
    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        job.max_attempts = 2
        await session.commit()

    worker = IngestionJobWorker(concurrency=1)

    # Attempt 1 -> RETRY with future backoff; doc back to queued
    assert await worker.run_once(sessions) == 1
    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        doc = (
            await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == "doc-1")
            )
        ).scalar_one()
        assert job.status == "RETRY"
        assert job.attempts == 1
        assert job.next_retry_time > _utcnow()
        assert "milvus down" in job.error_message
        assert doc.status == "queued"

    # Not due yet: worker must not claim it
    assert await worker.run_once(sessions) == 0

    # Force due, attempt 2 exhausts max_attempts -> DEAD, doc failed
    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        job.next_retry_time = _utcnow() - timedelta(minutes=1)
        await session.commit()

    assert await worker.run_once(sessions) == 1
    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        doc = (
            await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == "doc-1")
            )
        ).scalar_one()
        assert job.status == "DEAD"
        assert job.attempts == 2
        assert doc.status == "failed"


@pytest.mark.asyncio
async def test_worker_dead_on_missing_source_file(tmp_path):
    sessions, job_id, source = await _make_env(tmp_path)
    import os

    os.remove(source)

    assert await IngestionJobWorker(concurrency=1).run_once(sessions) == 1
    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        assert job.status == "DEAD"
        assert "source file missing" in job.error_message


@pytest.mark.asyncio
async def test_worker_reclaims_stale_running_job(tmp_path, monkeypatch):
    sessions, job_id, _ = await _make_env(tmp_path)

    # Simulate a crashed worker: RUNNING with an expired claim
    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        job.status = "RUNNING"
        job.claimed_by = "dead-host:1"
        job.claimed_at = _utcnow() - timedelta(hours=2)
        await session.commit()

    async def fake_execute(db, *, kb, doc, **kwargs):
        doc.status = "success"
        doc.chunk_count = 1
        return 1

    monkeypatch.setattr(
        "app.services.ingestion_executor.execute_ingestion", fake_execute
    )
    assert await IngestionJobWorker(concurrency=1).run_once(sessions) == 1
    async with sessions() as session:
        job = (
            await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        assert job.status == "SUCCESS"
        assert job.claimed_by != "dead-host:1"
