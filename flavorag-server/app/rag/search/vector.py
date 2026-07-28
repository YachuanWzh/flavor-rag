"""Milvus vector search channel."""

from __future__ import annotations

import asyncio
from functools import partial
from pymilvus import (
    Collection,
    connections,
    utility,
    FieldSchema,
    CollectionSchema,
    DataType,
)

from app.config.settings import settings
from app.llm.embedding import get_embedding_client
from app.rag.search.base import SearchChannel, SearchResult


class EmbeddingDimensionMismatch(RuntimeError):
    """Active index generation is incompatible with supplied vectors."""

    def __init__(self, collection_name: str, expected: int, actual: int):
        super().__init__(
            f"embedding dimension mismatch for {collection_name}: "
            f"active generation expects {expected}, received {actual}; "
            "build and promote a new index generation"
        )
        self.collection_name = collection_name
        self.expected = expected
        self.actual = actual


class MilvusSearchChannel(SearchChannel):
    """Vector search using Milvus with cosine similarity."""

    def __init__(self):
        self._connected = False

    def _connect(self):
        if not self._connected:
            connections.connect(alias="default", uri=settings.milvus_uri)
            self._connected = True

    # ---- collection lifecycle ----

    def create_collection(
        self,
        collection_name: str,
        *,
        dim: int | None = None,
    ) -> Collection:
        """Create a new Milvus collection.

        Always uses settings.embedding_dim (currently 4096).
        Drops and recreates if a collection with the same name already exists.
        """
        self._connect()
        target_dim = dim or settings.embedding_dim
        full_name = f"rag_{collection_name}"

        if utility.has_collection(full_name):
            return Collection(full_name)

        schema = CollectionSchema(
            fields=[
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=target_dim),
            ],
            description=f"RAG collection: {collection_name}",
        )
        collection = Collection(name=full_name, schema=schema)
        collection.create_index(
            field_name="embedding",
            index_params={
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128},
            },
        )
        collection.load()
        return collection

    def drop_collection(self, collection_name: str):
        """Drop a Milvus collection (release first)."""
        self._connect()
        full_name = f"rag_{collection_name}"
        if not utility.has_collection(full_name):
            return
        try:
            Collection(full_name).release()
        except Exception:
            pass
        utility.drop_collection(full_name)

    # ---- read helpers ----

    def get_collection(self, collection_name: str) -> Collection | None:
        self._connect()
        full_name = f"rag_{collection_name}"
        if not utility.has_collection(full_name):
            return None
        collection = Collection(full_name)
        collection.load()
        return collection

    @staticmethod
    def _collection_dim(collection: Collection) -> int | None:
        try:
            for field in collection.schema.fields:
                if field.name == "embedding" and hasattr(field, "params"):
                    return field.params.get("dim")
        except Exception:
            pass
        return None

    # ---- search ----

    async def search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 10,
        embedding_model: str | None = None,
    ) -> list[SearchResult]:
        collection = await asyncio.to_thread(
            self.get_collection, collection_name
        )
        if collection is None:
            return []

        embedder = get_embedding_client(model=embedding_model)
        query_vector = await embedder.embed_query(query)

        results = await asyncio.to_thread(
            partial(
                collection.search,
                data=[query_vector],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                limit=top_k,
                output_fields=["chunk_id", "content", "doc_id"],
            )
        )

        return [
            SearchResult(
                chunk_id=hit.entity.get("chunk_id", ""),
                doc_id=hit.entity.get("doc_id", ""),
                content=hit.entity.get("content", ""),
                score=float(hit.score),
            )
            for hits in results
            for hit in hits
        ]

    # ---- insert ----

    def insert(
        self,
        collection_name: str,
        chunk_ids: list[str],
        doc_ids: list[str],
        contents: list[str],
        vectors: list[list[float]],
    ):
        collection = self.get_collection(collection_name)
        if collection is None:
            return

        # Auto-heal dimension mismatch: when the embedding model changes
        # (e.g. 1536 → 4096), the existing collection schema won't match
        # the new vectors. Drop and recreate with the correct dim.
        if vectors:
            actual_dim = len(vectors[0])
            schema_dim = self._collection_dim(collection)
            if schema_dim is not None and schema_dim != actual_dim:
                raise EmbeddingDimensionMismatch(
                    collection_name, schema_dim, actual_dim
                )

        collection.insert([
            {
                "chunk_id": chunk_ids[i],
                "doc_id": doc_ids[i],
                "content": contents[i],
                "embedding": vectors[i],
            }
            for i in range(len(chunk_ids))
        ])

    # ---- delete ----

    def delete_by_ids(self, collection_name: str, chunk_ids: list[str]):
        if not chunk_ids:
            return
        collection = self.get_collection(collection_name)
        if collection is None:
            return
        import json
        encoded = ", ".join(json.dumps(cid) for cid in chunk_ids)
        collection.delete(expr=f"chunk_id in [{encoded}]")
        collection.flush()

    def existing_chunk_ids(
        self, collection_name: str, chunk_ids: list[str]
    ) -> set[str]:
        """Return the subset physically present in an active collection."""
        if not chunk_ids:
            return set()
        collection = self.get_collection(collection_name)
        if collection is None:
            return set()
        import json

        found: set[str] = set()
        for start in range(0, len(chunk_ids), 500):
            batch = chunk_ids[start:start + 500]
            encoded = ", ".join(json.dumps(value) for value in batch)
            rows = collection.query(
                expr=f"chunk_id in [{encoded}]",
                output_fields=["chunk_id"],
                limit=len(batch),
            )
            found.update(
                str(row["chunk_id"])
                for row in rows
                if row.get("chunk_id")
            )
        return found

    def all_chunk_ids(self, collection_name: str) -> set[str]:
        """Stream all physical chunk IDs for orphan reconciliation."""
        collection = self.get_collection(collection_name)
        if collection is None:
            return set()
        iterator = collection.query_iterator(
            batch_size=500,
            expr="chunk_id != ''",
            output_fields=["chunk_id"],
        )
        found: set[str] = set()
        try:
            while True:
                rows = iterator.next()
                if not rows:
                    break
                found.update(
                    str(row["chunk_id"])
                    for row in rows
                    if row.get("chunk_id")
                )
        finally:
            iterator.close()
        return found
