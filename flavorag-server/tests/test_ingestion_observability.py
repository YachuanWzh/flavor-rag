from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ingestion.nodes.base import NodeResult
from app.ingestion.pipeline_engine import IngestionEngine, validate_pipeline_graph
from app.models import (
    Base,
    IngestionPipeline,
    IngestionPipelineNode,
    IngestionTask,
    IngestionTaskNode,
)


def _node(node_id: str, node_type: str, next_node_id: str | None = None):
    return SimpleNamespace(
        node_id=node_id,
        node_type=node_type,
        next_node_id=next_node_id,
    )


def test_pipeline_graph_validation_fails_closed():
    validate_pipeline_graph(
        [_node("fetch", "fetcher", "parse"), _node("parse", "parser")]
    )

    with pytest.raises(ValueError, match="循环"):
        validate_pipeline_graph(
            [_node("a", "fetcher", "b"), _node("b", "parser", "a")]
        )
    with pytest.raises(ValueError, match="不存在"):
        validate_pipeline_graph([_node("a", "fetcher", "missing")])
    with pytest.raises(ValueError, match="不支持"):
        validate_pipeline_graph([_node("a", "arbitrary_code")])


@pytest.mark.asyncio
async def test_engine_records_trace_heartbeat_and_node_retry(monkeypatch):
    import app.ingestion.pipeline_engine as engine_module

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    calls = 0

    async def flaky_handler(context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary network issue")
        context.chunks.append({"content": "ok"})
        return NodeResult(status="success", message="recovered")

    monkeypatch.setitem(engine_module._NODE_HANDLERS, "fetcher", flaky_handler)
    async with sessions() as session:
        session.add(
            IngestionPipeline(
                id="pipeline-1",
                tenant_id="tenant-a",
                name="test",
                enabled=1,
                created_by="user-a",
            )
        )
        session.add(
            IngestionPipelineNode(
                id="node-row-1",
                pipeline_id="pipeline-1",
                node_id="fetch",
                node_type="fetcher",
                settings_json={"retry_backoff_ms": 0, "max_retries": 2},
                created_by="user-a",
            )
        )
        await session.commit()

        result = await IngestionEngine().execute_pipeline(
            pipeline_id="pipeline-1",
            source_type="file",
            source_location="/tmp/example.md",
            user_id="user-a",
            tenant_id="tenant-a",
            idempotency_key="stable-key",
            db=session,
        )
        await session.commit()
        task = (
            await session.execute(
                select(IngestionTask).where(IngestionTask.id == result.task_id)
            )
        ).scalar_one()
        task_node = (
            await session.execute(
                select(IngestionTaskNode).where(
                    IngestionTaskNode.task_id == result.task_id
                )
            )
        ).scalar_one()

    assert result.status == "success"
    assert calls == 2
    assert task.tenant_id == "tenant-a"
    assert task.trace_id
    assert task.heartbeat_at
    assert task.total_duration_ms is not None
    assert task.logs_json["sla_breached"] is False
    assert task_node.attempt == 2
    assert task_node.started_at and task_node.completed_at
    await engine.dispose()


@pytest.mark.asyncio
async def test_watchdog_converts_stale_running_task_to_timeout(monkeypatch):
    import app.services.ingestion_watchdog as watchdog_module

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(watchdog_module, "async_session_factory", sessions)
    stale = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20)
    )

    async with sessions() as session:
        session.add(
            IngestionTask(
                id="stale-task",
                tenant_id="tenant-a",
                pipeline_id="pipeline-1",
                source_type="file",
                source_location="/tmp/example.md",
                status="running",
                sla_ms=60_000,
                heartbeat_at=stale,
                started_at=stale,
                created_by="user-a",
            )
        )
        await session.commit()

    assert await watchdog_module.IngestionWatchdog().sweep() == 1
    async with sessions() as session:
        task = (
            await session.execute(
                select(IngestionTask).where(IngestionTask.id == "stale-task")
            )
        ).scalar_one()
    assert task.status == "timeout"
    assert "watchdog_timeout" in task.error_message
    assert task.completed_at is not None
    await engine.dispose()
