"""Retry worker for visible external-index repair jobs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.config.logging_config import get_logger
from app.models import IndexRepairJob, KnowledgeBase, KnowledgeChunk
from app.observability.metrics import INDEX_REPAIR_JOBS

_log = get_logger("flavorag.index_repair")


class IndexRepairWorker:
    def __init__(self, poll_interval_sec: int = 15):
        self.poll_interval_sec = poll_interval_sec
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        from app.database.session import async_session_factory

        while self._running:
            try:
                processed = await self.run_once(async_session_factory)
            except Exception as exc:
                processed = 0
                _log.warning("index_repair_poll_failed", error=str(exc))
            if not processed:
                await asyncio.sleep(self.poll_interval_sec)

    async def run_once(self, session_factory) -> int:
        async with session_factory() as session:
            jobs = list(
                (
                    await session.execute(
                        select(IndexRepairJob)
                        .where(
                            IndexRepairJob.deleted == 0,
                            IndexRepairJob.status.in_(["QUEUED", "RETRY"]),
                            or_(
                                IndexRepairJob.next_retry_time.is_(None),
                                IndexRepairJob.next_retry_time
                                <= datetime.now(timezone.utc).replace(
                                    tzinfo=None
                                ),
                            ),
                        )
                        .order_by(IndexRepairJob.create_time)
                        .limit(4)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars().all()
            )
            for job in jobs:
                job.status = "RUNNING"
                job.attempts = (job.attempts or 0) + 1
            await session.commit()

        for job in jobs:
            await self._process(session_factory, job.id)
        return len(jobs)

    async def _process(self, session_factory, job_id: str) -> None:
        async with session_factory() as session:
            job = (
                await session.execute(
                    select(IndexRepairJob).where(IndexRepairJob.id == job_id)
                )
            ).scalar_one()
            kb = (
                await session.execute(
                    select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id)
                )
            ).scalar_one()
            chunks = list(
                (
                    await session.execute(
                        select(KnowledgeChunk).where(
                            KnowledgeChunk.doc_id == job.doc_id,
                            KnowledgeChunk.generation == job.generation,
                            KnowledgeChunk.index_status == "ACTIVE",
                            KnowledgeChunk.deleted == 0,
                        )
                    )
                ).scalars().all()
            )
            try:
                if job.channel == "elasticsearch":
                    from app.rag.search.keyword import ESKeywordSearchChannel

                    await ESKeywordSearchChannel().insert(
                        [
                            {
                                "id": chunk.id,
                                "tenant_id": chunk.tenant_id,
                                "department_id": chunk.department_id,
                                "doc_id": chunk.doc_id,
                                "content": chunk.content,
                                "embedding_content": chunk.embedding_content,
                                "chunk_index": chunk.chunk_index,
                                "block_type": chunk.block_type,
                                "page_start": chunk.page_start,
                                "page_end": chunk.page_end,
                            }
                            for chunk in chunks
                        ],
                        kb.id,
                    )
                elif job.channel == "graph":
                    from app.rag.graph.neo4j_store import Neo4jGraphStore

                    await Neo4jGraphStore().upsert_chunks(
                        kb_id=kb.id,
                        collection_name=(
                            kb.active_collection_name or kb.collection_name
                        ),
                        chunks=[
                            {
                                "doc_id": chunk.doc_id,
                                "chunk_id": chunk.id,
                                "tenant_id": chunk.tenant_id,
                                "content": chunk.content,
                            }
                            for chunk in chunks
                        ],
                    )
                elif job.channel == "milvus":
                    from app.llm.embedding import get_embedding_client
                    from app.rag.search.vector import MilvusSearchChannel

                    texts = [
                        chunk.embedding_content or chunk.content
                        for chunk in chunks
                    ]
                    vectors = await get_embedding_client(
                        model=kb.embedding_model
                    ).embed_documents(texts)
                    collection = (
                        kb.active_collection_name or kb.collection_name
                    )
                    channel = MilvusSearchChannel()
                    chunk_ids = [chunk.id for chunk in chunks]
                    await asyncio.to_thread(
                        channel.delete_by_ids, collection, chunk_ids
                    )
                    await asyncio.to_thread(
                        channel.insert,
                        collection,
                        chunk_ids,
                        [chunk.doc_id for chunk in chunks],
                        [chunk.content for chunk in chunks],
                        vectors,
                    )
                else:
                    raise ValueError(f"unknown repair channel: {job.channel}")
                job.status = "SUCCESS"
                job.last_error = None
                job.next_retry_time = None
                INDEX_REPAIR_JOBS.labels(
                    channel=job.channel, result="success"
                ).inc()
            except Exception as exc:
                job.last_error = str(exc)[:2000]
                job.status = (
                    "DEAD"
                    if job.attempts >= (job.max_attempts or 5)
                    else "RETRY"
                )
                if job.status == "RETRY":
                    job.next_retry_time = (
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        + timedelta(
                            seconds=min(900, 2 ** min(job.attempts, 9))
                        )
                    )
                INDEX_REPAIR_JOBS.labels(
                    channel=job.channel, result=job.status.lower()
                ).inc()
            await session.commit()
