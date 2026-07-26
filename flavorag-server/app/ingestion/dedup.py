"""Duplicate document detection — content hash + optional semantic similarity."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import get_logger

_log = get_logger("flavorag.ingestion.dedup")


def compute_content_hash(file_path: str, algorithm: str = "sha256") -> str:
    """Compute a content-addressed hash of a file."""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def compute_content_hash_bytes(data: bytes, algorithm: str = "sha256") -> str:
    """Compute a content-addressed hash of bytes content."""
    return hashlib.new(algorithm, data).hexdigest()


@dataclass
class DuplicateCheckResult:
    is_duplicate: bool
    existing_doc_id: str = ""
    existing_doc_name: str = ""
    existing_kb_id: str = ""
    existing_status: str = ""
    hash_type: str = ""
    details: dict = field(default_factory=dict)


class DuplicateDetector:
    """Detect duplicate documents within a knowledge base.

    Two-tier strategy:
      1. Fast: content hash (SHA-256) lookup against existing documents
      2. Deep: (future) semantic similarity via embedding cosine distance
    """

    ASYNC_PREFIX = "asyncpg:"

    def __init__(self, threshold: float = 0.98):
        self.threshold = threshold  # cosine similarity threshold (future use)

    async def check_file(
        self,
        file_path: str,
        kb_id: str,
        db: AsyncSession,
        *,
        tenant_id: str = "default",
    ) -> DuplicateCheckResult:
        """Check if a file is a duplicate of an existing document in the KB.

        Returns DuplicateCheckResult with is_duplicate=True if found.
        """
        content_hash = compute_content_hash(file_path)
        return await self._check_hash(content_hash, kb_id, db, tenant_id)

    async def check_bytes(
        self,
        content: bytes,
        kb_id: str,
        db: AsyncSession,
        *,
        tenant_id: str = "default",
    ) -> DuplicateCheckResult:
        """Check if raw bytes already exist in the KB."""
        content_hash = compute_content_hash_bytes(content)
        return await self._check_hash(content_hash, kb_id, db, tenant_id)

    async def _check_hash(
        self,
        content_hash: str,
        kb_id: str,
        db: AsyncSession,
        tenant_id: str,
    ) -> DuplicateCheckResult:
        """Single-hash lookup against t_knowledge_document.content_hash."""
        from app.models import KnowledgeDocument

        result = await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.kb_id == kb_id,
                KnowledgeDocument.content_hash == content_hash,
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.deleted == 0,
            ).limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            _log.info(
                "duplicate_detected",
                doc_id=existing.id,
                doc_name=existing.doc_name,
                hash_type="sha256",
            )
            return DuplicateCheckResult(
                is_duplicate=True,
                existing_doc_id=existing.id,
                existing_doc_name=existing.doc_name or "",
                existing_kb_id=existing.kb_id or "",
                existing_status=existing.status or "",
                hash_type="sha256",
                details={"content_hash": content_hash},
            )
        return DuplicateCheckResult(
            is_duplicate=False,
            hash_type="sha256",
            details={"content_hash": content_hash},
        )

    async def check_semantic(
        self,
        text: str,
        kb_id: str,
        db: AsyncSession,
        *,
        tenant_id: str = "default",
    ) -> DuplicateCheckResult:
        """Semantic similarity check against existing document embeddings.

        This is an optional deep check for near-duplicate detection. It queries
        existing document chunks and compares embedding distances.
        """
        if not text or len(text.strip()) < 50:
            return DuplicateCheckResult(is_duplicate=False)

        try:
            from app.llm.embedding import get_embedding_client

            embedder = get_embedding_client()
            query_vector = await embedder.embed_query(text[:8000])

            # Find the closest existing chunk in the KB via postgres vector distance
            # (Milvus is used for retrieval; PG vector is used for dedup)
            async_pg = await db.get_bind()
            pg_url = str(async_pg.url).replace("+asyncpg", "")

            from sqlalchemy import create_engine
            import psycopg2
            import json

            sync_url = pg_url.replace("postgresql://", "postgresql://", 1)
            # We use async session's connection for the vector query
            vector_str = json.dumps(query_vector)
            pg_result = await db.execute(
                text(
                    """
                    SELECT kd.id, kd.doc_name,
                           1 - (kc.embedding_vector <=> :qv) AS similarity
                    FROM t_knowledge_chunk kc
                    JOIN t_knowledge_document kd ON kd.id = kc.doc_id AND kd.deleted = 0
                    WHERE kd.kb_id = :kb_id
                      AND kd.tenant_id = :tenant_id
                      AND kd.deleted = 0
                      AND kc.deleted = 0
                      AND kc.embedding_vector IS NOT NULL
                    ORDER BY similarity DESC
                    LIMIT 1
                    """
                ),
                {"qv": vector_str, "kb_id": kb_id, "tenant_id": tenant_id},
            )
            row = pg_result.fetchone()
            if row and row.similarity >= self.threshold:
                _log.info(
                    "semantic_duplicate_detected",
                    doc_id=row.id,
                    similarity=round(float(row.similarity), 4),
                )
                return DuplicateCheckResult(
                    is_duplicate=True,
                    existing_doc_id=row.id,
                    existing_doc_name=row.doc_name or "",
                    hash_type="semantic",
                    details={"similarity": round(float(row.similarity), 4)},
                )
        except Exception as exc:
            _log.warning("semantic_dedup_failed", error=str(exc))

        return DuplicateCheckResult(is_duplicate=False)


def find_duplicates_in_batch(
    file_paths: Sequence[str],
) -> dict[str, list[str]]:
    """Quick in-memory duplicate detection within a single batch.

    Returns a dict mapping the canonical file path to
    the list of duplicate file paths.
    """
    seen: dict[str, str] = {}
    canonical: dict[str, list[str]] = {}
    for fp in file_paths:
        h = hashlib.sha256()
        try:
            with open(fp, "rb") as f:
                while chunk := f.read(8192):
                    h.update(chunk)
        except Exception:
            continue
        digest = h.hexdigest()
        if digest in seen:
            canonical[seen[digest]].append(fp)
        else:
            seen[digest] = fp
            canonical[fp] = []
    return canonical
