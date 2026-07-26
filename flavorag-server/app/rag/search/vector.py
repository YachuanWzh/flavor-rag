"""Milvus vector search channel."""

from __future__ import annotations

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
            self.drop_collection(collection_name)

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
    ) -> list[SearchResult]:
        collection = self.get_collection(collection_name)
        if collection is None:
            return []

        embedder = get_embedding_client()
        query_vector = await embedder.embed_query(query)

        results = collection.search(
            data=[query_vector],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k,
            output_fields=["chunk_id", "content", "doc_id"],
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
