"""Lease and ownership behavior for durable evaluation workers."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, EvaluationRun
from app.services.evaluation_jobs import EvaluationJobWorker


async def _make_env(tmp_path):
    database = (tmp_path / "evaluation-jobs.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            EvaluationRun(
                id="eval-1",
                tenant_id="default",
                kb_id="*",
                kb_name="all",
                dataset_version="test",
                status="queued",
                gate_status="pending",
                config_json={},
                created_by="user-1",
            )
        )
        await session.commit()
    return engine, sessions


@pytest.mark.asyncio
async def test_heartbeat_prevents_long_run_from_being_reclaimed(
    tmp_path, monkeypatch
):
    engine, sessions = await _make_env(tmp_path)
    release = asyncio.Event()

    async def fake_execute(
        run_id, session_factory, *, expected_worker_id
    ):
        await release.wait()
        async with session_factory() as session:
            record = await session.get(EvaluationRun, run_id)
            assert record.claimed_by == expected_worker_id
            record.status = "completed"
            record.claimed_by = None
            record.claimed_at = None
            await session.commit()

    monkeypatch.setattr(
        "app.services.evaluation_jobs.execute_evaluation_run",
        fake_execute,
    )
    owner = EvaluationJobWorker(
        heartbeat_interval_sec=0.01,
        lease_timeout_sec=0.05,
    )
    owner_task = asyncio.create_task(owner.run_once(sessions))

    first_claim = None
    for _ in range(100):
        async with sessions() as session:
            record = await session.get(EvaluationRun, "eval-1")
            if record.claimed_at is not None:
                if first_claim is None:
                    first_claim = record.claimed_at
                elif record.claimed_at > first_claim:
                    break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("evaluation lease heartbeat was not persisted")

    competitor = EvaluationJobWorker(
        heartbeat_interval_sec=0.01,
        lease_timeout_sec=0.05,
    )
    assert await competitor.run_once(sessions) == 0

    release.set()
    assert await owner_task == 1
    async with sessions() as session:
        record = await session.get(EvaluationRun, "eval-1")
        assert record.status == "completed"
        assert record.attempts == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_old_owner_cannot_overwrite_new_owner_failure_state(
    tmp_path, monkeypatch
):
    engine, sessions = await _make_env(tmp_path)

    async def lose_lease_then_fail(
        run_id, session_factory, *, expected_worker_id
    ):
        async with session_factory() as session:
            record = await session.get(EvaluationRun, run_id)
            assert record.claimed_by == expected_worker_id
            record.claimed_by = "replacement-worker"
            await session.commit()
        raise RuntimeError("stale worker failure")

    monkeypatch.setattr(
        "app.services.evaluation_jobs.execute_evaluation_run",
        lose_lease_then_fail,
    )
    worker = EvaluationJobWorker(heartbeat_interval_sec=60)
    assert await worker.run_once(sessions) == 1

    async with sessions() as session:
        record = await session.get(EvaluationRun, "eval-1")
        assert record.status == "running"
        assert record.claimed_by == "replacement-worker"
        assert record.error_message is None
    await engine.dispose()
