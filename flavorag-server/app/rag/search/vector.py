"""Milvus vector search channel."""
from __future__ import annotations

from pymilvus import Collection, connections, FieldSchema, CollectionSchema, DataType

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

    def create_collection(
        self,
        collection_name: str,
        dim: int | None = None,
        drop_if_exists: bool = False,
    ) -> Collection:
        """Create a Milvus collection for a knowledge base.

        Collection name format: rag_{collection_name}
        When dim is None, reads the actual dimension from the configured
        embedding client. After the first real embedding call, the client
        auto-corrects its dim field from the API response.
        """
        self._connect()
        if dim is None:
            dim = get_embedding_client().dim
        full_name = f"rag_{collection_name}"

        if drop_if_exists:
            from pymilvus import utility
            if utility.has_collection(full_name):
                utility.drop_collection(full_name)

        schema = CollectionSchema(
            fields=[
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
            ],
            description=f"RAG collection: {collection_name}",
        )

        collection = Collection(name=full_name, schema=schema)

        # Create IVF_FLAT index for cosine similarity
        index_params = {
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        collection.create_index(field_name="embedding", index_params=index_params)
        collection.load()

        return collection

    def get_collection(self, collection_name: str) -> Collection | None:
        """Get (load if needed) an existing Milvus collection.
        Returns None if the collection does not exist."""
        self._connect()
        from pymilvus import utility
        full_name = f"rag_{collection_name}"
        if not utility.has_collection(full_name):
            return None
        collection = Collection(full_name)
        collection.load()
        return collection

    async def search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Vector similarity search. Returns empty list if collection missing."""
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

        search_results: list[SearchResult] = []
        for hits in results:
            for hit in hits:
                search_results.append(SearchResult(
                    chunk_id=hit.entity.get("chunk_id", ""),
                    doc_id=hit.entity.get("doc_id", ""),
                    content=hit.entity.get("content", ""),
                    score=float(hit.score),
                ))

        return search_results

    def insert(
        self,
        collection_name: str,
        chunk_ids: list[str],
        doc_ids: list[str],
        contents: list[str],
        vectors: list[list[float]],
    ):
        """Insert vectors with metadata into a collection."""
        collection = self.get_collection(collection_name)
        if collection is None:
            return
        # Use dict-based insert to avoid pymilvus positional field mapping issues
        data = [
            {
                "chunk_id": chunk_ids[i],
                "doc_id": doc_ids[i],
                "content": contents[i],
                "embedding": vectors[i],
            }
            for i in range(len(chunk_ids))
        ]
        collection.insert(data)

    def drop_collection(self, collection_name: str):
        """Drop a collection."""
        self._connect()
        from pymilvus import utility
        full_name = f"rag_{collection_name}"
        if utility.has_collection(full_name):
            utility.drop_collection(full_name)
