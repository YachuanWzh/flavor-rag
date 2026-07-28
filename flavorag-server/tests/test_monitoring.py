from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.monitoring import (
    list_ingestion_jobs,
    monitoring_summary,
    monitoring_timeseries,
)
from app.models import Base, IngestionTask, User


@pytest.mark.asyncio
async def test_monitoring_includes_visual_pipeline_tasks():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    admin = User(
        id="admin-1",
        username="admin",
        password="not-used",
        role="admin",
        tenant_id="default",
    )

    async with sessions() as session:
        session.add(
            IngestionTask(
                id="pipeline-task-1",
                tenant_id="default",
                pipeline_id="pipeline-1",
                source_type="file",
                source_location="/tmp/example.pdf",
                source_file_name="example.pdf",
                status="success",
                total_duration_ms=1200,
                chunk_count=8,
                completed_at=now,
                created_by="admin-1",
                create_time=now,
            )
        )
        await session.commit()

        summary = await monitoring_summary(hours=24, db=session, user=admin)
        assert summary["data"]["ingestion"]["queue"]["SUCCESS"] == 1
        assert (
            summary["data"]["ingestion"]["sources"]["pipelineTasks"]["SUCCESS"]
            == 1
        )

        series = await monitoring_timeseries(
            hours=24, buckets=4, db=session, user=admin
        )
        assert sum(point["jobsCompleted"] for point in series["data"]["points"]) == 1

        recent = await list_ingestion_jobs(
            status="", page=1, limit=20, db=session, user=admin
        )
        assert recent["data"]["total"] == 1
        assert recent["data"]["items"][0]["source"] == "pipeline_task"
        assert recent["data"]["items"][0]["docName"] == "example.pdf"

    await engine.dispose()
