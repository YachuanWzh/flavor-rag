"""Document ingestion pipeline — Parse → Chunk → Embed → Index → PG save."""
from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.ingestion.parser import DocumentParser
from app.ingestion.chunker import DocumentChunker, ChunkConfig
from app.llm.embedding import get_embedding_client
from app.rag.search.vector import MilvusSearchChannel


class IngestionPipeline:
    """Orchestrates the full document ingestion workflow.

    Flow: Parse → Chunk → Embed → Index(Milvus) → Save(PG)
    """

    def __init__(self):
        self.parser = DocumentParser()
        self.chunker = DocumentChunker()
        self.embedder = get_embedding_client()
        self.milvus = MilvusSearchChannel()

    async def run(
        self,
        doc_id: str,
        kb_id: str,
        file_path: str,
        collection_name: str,
        db: AsyncSession,
        chunk_config: ChunkConfig | None = None,
    ) -> int:
        """Run the full ingestion pipeline.

        Returns the number of chunks created.
        """
        from app.models import KnowledgeChunk, KnowledgeDocument

        # 1. Parse
        parsed_text = await self.parser.parse(file_path)

        # 2. Chunk
        if chunk_config is None:
            chunk_config = ChunkConfig()
        chunks = self.chunker.chunk(parsed_text, chunk_config)

        if not chunks:
            return 0

        # 3. Embed
        texts = [c["content"] for c in chunks]
        vectors = await self.embedder.embed_documents(texts)

        # 4. Save chunk metadata to PostgreSQL
        chunk_records: list[KnowledgeChunk] = []
        for c in chunks:
            content = c["content"]
            chunk_records.append(KnowledgeChunk(
                kb_id=kb_id,
                doc_id=doc_id,
                chunk_index=c["chunk_index"],
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                char_count=c["char_count"],
                created_by="system",
            ))

        for cr in chunk_records:
            db.add(cr)
        await db.flush()

        # 5. Insert vectors into Milvus
        chunk_ids = [cr.id for cr in chunk_records]
        doc_ids = [doc_id] * len(chunk_records)
        contents = [cr.content for cr in chunk_records]

        self.milvus.insert(
            collection_name=collection_name,
            chunk_ids=chunk_ids,
            doc_ids=doc_ids,
            contents=contents,
            vectors=vectors,
        )

        # 6. Update document status
        from sqlalchemy import select
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.chunk_count = len(chunks)
            doc.status = "success"

        # 7. Optional: ES keyword index
        if settings.es_enabled:
            await self._index_to_es(kb_id, chunk_records)

        # 8. Optional: LightRAG graph sync
        if settings.graph_enabled:
            await self._sync_to_lightrag(kb_id, chunk_records)

        return len(chunks)

    async def _index_to_es(self, kb_id: str, chunks: list):
        """Index chunk content to Elasticsearch for BM25 keyword search."""
        try:
            from elasticsearch import AsyncElasticsearch
            es = AsyncElasticsearch(settings.es_uris)
            for c in chunks:
                await es.index(
                    index="rag_keyword_store",
                    id=c.id,
                    body={
                        "kb_id": kb_id,
                        "content": c.content,
                        "chunk_index": c.chunk_index,
                    },
                )
        except Exception:
            pass  # ES is optional; silently skip on failure

    async def _sync_to_lightrag(self, kb_id: str, chunks: list):
        """Sync chunks to LightRAG knowledge graph."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                for c in chunks:
                    await client.post(
                        f"{settings.lightrag_base_url}/documents",
                        json={"kb_id": kb_id, "content": c.content},
                    )
        except Exception:
            pass  # LightRAG is optional; silently skip
