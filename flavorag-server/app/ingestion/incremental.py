"""Incremental indexing — change detection and selective re-indexing."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.dedup import compute_content_hash
from app.config.logging_config import get_logger
from app.models import KnowledgeDocument, KnowledgeChunk

_log = get_logger("flavorag.ingestion.incremental")


@dataclass
class ChangeDetectionResult:
    changed: bool
    content_hash: str = ""
    reason: str = ""


class IncrementalIndexer:
    """Detect document changes and re-index only changed content.

    Strategy:
      1. Compute SHA-256 hash of the current file
      2. Compare against stored content_hash on the document record
      3. If unchanged, skip re-indexing entirely
      4. If changed, soft-delete old chunks and run full ingestion
    """

    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        return compute_content_hash(file_path)

    @staticmethod
    def compute_bytes_hash(data: bytes) -> str:
        from app.ingestion.dedup import compute_content_hash_bytes
        return compute_content_hash_bytes(data)

    @staticmethod
    async def check_document_changed(
        file_path: str,
        doc_id: str,
        db: AsyncSession,
    ) -> ChangeDetectionResult:
        """Check if a document's content has changed since last ingestion.

        Returns ChangeDetectionResult with changed=True if re-indexing is needed.
        """
        if not os.path.exists(file_path):
            return ChangeDetectionResult(
                changed=True,
                reason="file_not_found",
            )

        new_hash = compute_content_hash(file_path)

        result = await db.execute(
            select(KnowledgeDocument.content_hash).where(
                KnowledgeDocument.id == doc_id,
                KnowledgeDocument.deleted == 0,
            )
        )
        row = result.first()
        old_hash = row[0] if row else ""

        if not old_hash:
            return ChangeDetectionResult(
                changed=True,
                content_hash=new_hash,
                reason="no_existing_hash",
            )

        if new_hash == old_hash:
            return ChangeDetectionResult(
                changed=False,
                content_hash=new_hash,
                reason="content_unchanged",
            )

        return ChangeDetectionResult(
            changed=True,
            content_hash=new_hash,
            reason="content_changed",
        )

    @staticmethod
    async def update_document_hash(
        doc_id: str,
        content_hash: str,
        db: AsyncSession,
    ) -> None:
        """Persist the new content hash after successful ingestion."""
        await db.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == doc_id)
            .values(content_hash=content_hash)
        )

    @staticmethod
    async def soft_delete_old_chunks(
        doc_id: str,
        db: AsyncSession,
    ) -> int:
        """Soft-delete all active chunks for a document. Returns count of deleted chunks."""
        result = await db.execute(
            update(KnowledgeChunk)
            .where(
                KnowledgeChunk.doc_id == doc_id,
                KnowledgeChunk.deleted == 0,
            )
            .values(deleted=1)
        )
        return result.rowcount

    @staticmethod
    async def check_url_document_changed(
        content: bytes,
        doc_id: str,
        db: AsyncSession,
    ) -> ChangeDetectionResult:
        """Check if a URL-sourced document has changed by comparing content hashes."""
        from app.ingestion.dedup import compute_content_hash_bytes

        new_hash = compute_content_hash_bytes(content)

        result = await db.execute(
            select(KnowledgeDocument.content_hash).where(
                KnowledgeDocument.id == doc_id,
                KnowledgeDocument.deleted == 0,
            )
        )
        row = result.first()
        old_hash = row[0] if row else ""

        if not old_hash:
            return ChangeDetectionResult(
                changed=True,
                content_hash=new_hash,
                reason="no_existing_hash",
            )

        if new_hash == old_hash:
            return ChangeDetectionResult(
                changed=False,
                content_hash=new_hash,
                reason="content_unchanged",
            )

        return ChangeDetectionResult(
            changed=True,
            content_hash=new_hash,
            reason="content_changed",
        )
