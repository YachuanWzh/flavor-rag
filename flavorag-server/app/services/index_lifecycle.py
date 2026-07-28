"""Non-destructive vector-index generation build and atomic promotion."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.llm.embedding import get_embedding_client
from app.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeIndexGeneration,
    gen_id,
)
from app.rag.search.vector import MilvusSearchChannel
from app.services.schedule.lock_manager import ScheduleLockManager

_log = get_logger("flavorag.index_lifecycle")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IndexLifecycleService:
    async def plan(self, session, *, kb: KnowledgeBase, user_id: str) -> str:
        generation = f"g_{gen_id()}"
        physical = f"{kb.collection_name}__{generation}"
        record = KnowledgeIndexGeneration(
            id=gen_id(),
            kb_id=kb.id,
            generation=generation,
            collection_name=physical,
            embedding_model=kb.embedding_model,
            embedding_dim=0,
            parser_version="mixed",
            chunker_version="v0.0.5",
            status="BUILDING",
            created_by=user_id,
        )
        session.add(record)
        await session.flush()
        return record.id

    async def build_and_promote(self, generation_id: str) -> None:
        async with async_session_factory() as session:
            record = (
                await session.execute(
                    select(KnowledgeIndexGeneration).where(
                        KnowledgeIndexGeneration.id == generation_id,
                        KnowledgeIndexGeneration.deleted == 0,
                    )
                )
            ).scalar_one()
            kb = (
                await session.execute(
                    select(KnowledgeBase).where(
                        KnowledgeBase.id == record.kb_id,
                        KnowledgeBase.deleted == 0,
                    )
                )
            ).scalar_one()
            chunks = list(
                (
                    await session.execute(
                        select(KnowledgeChunk)
                        .where(
                            KnowledgeChunk.kb_id == kb.id,
                            KnowledgeChunk.index_status == "ACTIVE",
                            KnowledgeChunk.deleted == 0,
                            KnowledgeChunk.enabled != 0,
                        )
                        .order_by(
                            KnowledgeChunk.doc_id,
                            KnowledgeChunk.chunk_index,
                        )
                    )
                ).scalars().all()
            )
            record.expected_chunks = len(chunks)
            await session.commit()

        channel = MilvusSearchChannel()
        resolved_dim = 0
        try:
            if not chunks:
                raise RuntimeError("cannot build an index generation with zero chunks")
            embedder = get_embedding_client(model=record.embedding_model)
            indexed = 0
            for start in range(0, len(chunks), 64):
                batch = chunks[start : start + 64]
                texts = [
                    chunk.embedding_content or chunk.content for chunk in batch
                ]
                vectors = await embedder.embed_documents(texts)
                if not vectors:
                    continue
                if indexed == 0:
                    resolved_dim = len(vectors[0])
                    await asyncio.to_thread(
                        channel.create_collection,
                        record.collection_name,
                        dim=len(vectors[0]),
                    )
                await asyncio.to_thread(
                    channel.insert,
                    record.collection_name,
                    [chunk.id for chunk in batch],
                    [chunk.doc_id for chunk in batch],
                    [chunk.content for chunk in batch],
                    vectors,
                )
                indexed += len(batch)

            collection = await asyncio.to_thread(
                channel.get_collection, record.collection_name
            )
            if collection is not None:
                await asyncio.to_thread(collection.flush)
            actual = int(collection.num_entities) if collection is not None else 0
            if actual != len(chunks):
                raise RuntimeError(
                    f"index validation failed: expected {len(chunks)}, got {actual}"
                )

            async with async_session_factory() as session:
                current = (
                    await session.execute(
                        select(KnowledgeIndexGeneration).where(
                            KnowledgeIndexGeneration.id == generation_id
                        )
                    )
                ).scalar_one()
                kb = (
                    await session.execute(
                        select(KnowledgeBase).where(
                            KnowledgeBase.id == current.kb_id
                        )
                    )
                ).scalar_one()
                old = list(
                    (
                        await session.execute(
                            select(KnowledgeIndexGeneration).where(
                                KnowledgeIndexGeneration.kb_id == kb.id,
                                KnowledgeIndexGeneration.status == "ACTIVE",
                            )
                        )
                    ).scalars().all()
                )
                for item in old:
                    item.status = "RETIRED"
                current.status = "ACTIVE"
                current.embedding_dim = resolved_dim
                current.indexed_chunks = actual
                current.activated_at = _utcnow()
                kb.active_collection_name = current.collection_name
                kb.active_index_generation = current.generation
                await session.commit()
        except Exception as exc:
            async with async_session_factory() as session:
                current = (
                    await session.execute(
                        select(KnowledgeIndexGeneration).where(
                            KnowledgeIndexGeneration.id == generation_id
                        )
                    )
                ).scalar_one()
                current.status = "FAILED"
                current.error_message = str(exc)[:2000]
                await session.commit()
            _log.error(
                "index_generation_failed",
                generation_id=generation_id,
                error=str(exc),
            )
            raise


class IndexBuildWorker:
    """Durably resumes BUILDING generations instead of using request tasks."""

    def __init__(self, poll_interval_sec: int = 5):
        self.poll_interval_sec = poll_interval_sec
        self._running = False
        self._task: asyncio.Task | None = None
        self._lock = ScheduleLockManager()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name="index-build-worker"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            generation_id = None
            try:
                async with async_session_factory() as session:
                    generation_id = (
                        await session.execute(
                            select(KnowledgeIndexGeneration.id)
                            .where(
                                KnowledgeIndexGeneration.status == "BUILDING",
                                KnowledgeIndexGeneration.deleted == 0,
                            )
                            .order_by(
                                KnowledgeIndexGeneration.create_time
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                lock_name = f"index-build:{generation_id}"
                if generation_id and await self._lock.acquire(lock_name):
                    try:
                        await IndexLifecycleService().build_and_promote(
                            generation_id
                        )
                    finally:
                        await self._lock.release(lock_name)
            except Exception as exc:
                _log.warning(
                    "index_build_worker_failed",
                    generation_id=generation_id,
                    error=str(exc),
                )
            if not generation_id:
                await asyncio.sleep(self.poll_interval_sec)
