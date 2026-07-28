"""mem0 client — Milvus-backed long-term memory store with intelligent dedup.

Implements the mem0 pattern (extract → dedup → store → search) using the
project's existing infrastructure:
  - Milvus for vector storage (collection: user_memories)
  - DeepSeek-V4-Flash for fact extraction + memory conflict resolution
  - Qwen3-Embedding-8B via SiliconFlow for vectorization
  - Elasticsearch (optional) for keyword-based memory recall (hybrid search)

Each memory fact is tagged with user_id + tenant_id for per-user isolation.

Key mem0-aligned features:
  1. Memory Extraction — LLM extracts structured facts from conversations
  2. Memory Update & Dedup — before insert, LLM compares new facts vs existing
     ones and decides ADD / UPDATE / NOOP / DELETE (prevents memory inflation)
  3. Hybrid Retrieval — vector + keyword dual-channel recall with score fusion
  4. Auto Injection — caller injects returned memories into generation prompt
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.llm.embedding import get_embedding_client
from app.llm.client import MockLLMClient, get_llm_client
from app.rag.search.vector import EmbeddingDimensionMismatch

_log = get_logger("flavorag.memory.mem0")


# ── Milvus collection lifecycle ──


def _get_milvus_connection():
    """Return a connected Milvus alias (reuse the default connection)."""
    from pymilvus import connections

    if not connections.has_connection("default"):
        connections.connect(alias="default", uri=settings.milvus_uri)
    return "default"


def _ensure_collection(dim: int | None = None):
    """Create the user_memories collection in Milvus if it doesn't exist.

    Args:
        dim: embedding dimension. Falls back to settings.embedding_dim.
             Pass the actual vector dim to avoid mismatch.
    """
    from pymilvus import (
        Collection,
        utility,
        FieldSchema,
        CollectionSchema,
        DataType,
    )

    _get_milvus_connection()
    collection_name = settings.mem0_collection_name
    actual_dim = dim or settings.embedding_dim

    if utility.has_collection(collection_name):
        return Collection(collection_name)

    schema = CollectionSchema(
        fields=[
            FieldSchema(name="pk", dtype=DataType.VARCHAR, is_primary=True, max_length=128),
            FieldSchema(name="memory_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8192),
            FieldSchema(name="metadata", dtype=DataType.JSON),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=actual_dim),
        ],
        description="User long-term memory facts (mem0 pattern)",
    )
    collection = Collection(name=collection_name, schema=schema)
    collection.create_index(
        field_name="embedding",
        index_params={
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        },
    )
    collection.create_index(
        field_name="user_id",
        index_params={"index_type": "INVERTED"},
    )
    collection.load()
    _log.info("mem0_collection_created", collection=collection_name, dim=actual_dim)
    return collection


def _get_collection_dim(col) -> int | None:
    """Detect the embedding dimension from a Milvus collection schema."""
    try:
        for field in col.schema.fields:
            if field.name == "embedding" and hasattr(field, "params"):
                return field.params.get("dim")
    except Exception:
        pass
    return None


def _recreate_collection(actual_dim: int):
    """Drop and recreate the memory collection with the correct dim.

    Auto-heal: when the embedding model changes (e.g. 1536 → 4096),
    the existing collection schema won't match. Drop and recreate.
    """
    from pymilvus import Collection, utility

    _get_milvus_connection()
    collection_name = settings.mem0_collection_name

    if utility.has_collection(collection_name):
        try:
            Collection(collection_name).release()
        except Exception:
            pass
        utility.drop_collection(collection_name)
        _log.info("mem0_collection_dropped_for_recreate", collection=collection_name)

    return _ensure_collection(dim=actual_dim)


def _get_collection():
    """Get or create the memory collection, ensuring it's loaded."""
    from pymilvus import Collection, utility

    _get_milvus_connection()
    collection_name = settings.mem0_collection_name
    if not utility.has_collection(collection_name):
        return _ensure_collection()
    col = Collection(collection_name)
    col.load()
    return col


# ── LLM prompts ──

EXTRACT_SYSTEM_PROMPT = """你是一个记忆提取助手。分析用户与助手的对话，提取值得长期记住的用户事实。

提取规则：
1. 只提取关于用户的事实（偏好、习惯、背景、专业领域、常见需求等）
2. 忽略一次性的临时问题（如"今天天气怎样"）
3. 每条记忆用简洁的陈述句，不超过100字
4. 如果没有值得记住的事实，返回空数组
5. 返回 JSON 格式：{"memories": ["事实1", "事实2", ...]}

示例：
对话：
用户：我们公司的Java项目用的是Spring Boot 3.2，数据库是PostgreSQL
助手：了解了，您使用的是Spring Boot 3.2和PostgreSQL。
输出：{"memories": ["用户公司Java项目使用Spring Boot 3.2框架", "用户公司数据库使用PostgreSQL"]}
"""

DEDUP_SYSTEM_PROMPT = """你是一个记忆管理助手。你需要判断每条新记忆与已有记忆之间的关系。

对于每条新记忆，输出以下操作之一：
- "ADD"：新信息，与已有记忆不冲突且不重复
- "UPDATE"：新信息更新或推翻了某条已有记忆（如用户以前喜欢Java，现在改学Go）
- "NOOP"：与已有记忆重复，无需存储

如果操作是"UPDATE"，还需要指明要更新的已有记忆的ID。

返回 JSON 数组格式：
[
  {"memory": "新记忆内容", "action": "ADD"},
  {"memory": "新记忆内容", "action": "UPDATE", "old_memory_id": "要更新的记忆ID"},
  {"memory": "新记忆内容", "action": "NOOP"}
]
"""


# ── Fact extraction ──

async def _extract_facts(messages: list[dict]) -> list[str]:
    """Use DeepSeek-V4-Flash to extract memory facts from a conversation slice."""
    model = settings.mem0_model or "deepseek-v4-flash"
    base_url = (settings.mem0_base_url or "https://api.deepseek.com/v1").rstrip("/")
    api_key = settings.mem0_api_key or settings.hyde_api_key or settings.bailian_api_key

    client = get_llm_client(api_key=api_key, base_url=base_url, model=model)
    if isinstance(client, MockLLMClient):
        _log.debug("mem0_extract_skipped_mock", reason="no_api_key")
        return []

    conv_text = "\n".join(
        f"{m['role']}: {str(m.get('content', ''))[:500]}" for m in messages
    )

    prompt_messages = [
        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": f"对话内容：\n{conv_text}"},
    ]

    try:
        tokens: list[str] = []
        async with asyncio.timeout(settings.mem0_timeout_sec):
            async for token in client.chat_stream(prompt_messages, temperature=0.3):
                if not token.startswith("__THINK__"):
                    tokens.append(token)
                if len("".join(tokens)) > settings.mem0_max_tokens * 2:
                    break

        raw = "".join(tokens).strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start : end + 1])
            memories = data.get("memories", [])
            if isinstance(memories, list):
                return [str(m)[:200] for m in memories if m]

    except asyncio.TimeoutError:
        _log.warning("mem0_extract_timeout", model=model)
    except Exception as exc:
        _log.warning("mem0_extract_failed", error=str(exc)[:200])

    return []


# ── Memory dedup / conflict resolution ──

async def _resolve_conflicts(
    user_id: str,
    new_facts: list[str],
    existing_memories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare new facts against existing memories using LLM.

    Returns a list of decisions:
      {"memory": str, "action": "ADD" | "UPDATE" | "NOOP", "old_memory_id": str | None}
    """
    if not new_facts:
        return []
    if not existing_memories:
        # No existing memories — all are ADD
        return [{"memory": f, "action": "ADD", "old_memory_id": None} for f in new_facts]

    model = settings.mem0_model or "deepseek-v4-flash"
    base_url = (settings.mem0_base_url or "https://api.deepseek.com/v1").rstrip("/")
    api_key = settings.mem0_api_key or settings.hyde_api_key or settings.bailian_api_key

    client = get_llm_client(api_key=api_key, base_url=base_url, model=model)
    if isinstance(client, MockLLMClient):
        # In mock mode, just ADD everything
        return [{"memory": f, "action": "ADD", "old_memory_id": None} for f in new_facts]

    # Format existing memories for the LLM
    existing_text = "\n".join(
        f"[ID: {m['memory_id']}] {m['content']}" for m in existing_memories
    )
    new_text = "\n".join(f"- {f}" for f in new_facts)

    prompt_messages = [
        {"role": "system", "content": DEDUP_SYSTEM_PROMPT},
        {"role": "user", "content": f"已有记忆：\n{existing_text}\n\n新记忆：\n{new_text}"},
    ]

    try:
        tokens: list[str] = []
        async with asyncio.timeout(settings.mem0_timeout_sec):
            async for token in client.chat_stream(prompt_messages, temperature=0.1):
                if not token.startswith("__THINK__"):
                    tokens.append(token)
                if len("".join(tokens)) > settings.mem0_max_tokens * 2:
                    break

        raw = "".join(tokens).strip()
        # Parse JSON array
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            decisions = json.loads(raw[start : end + 1])
            if isinstance(decisions, list):
                result = []
                for item in decisions:
                    if not isinstance(item, dict):
                        continue
                    action = item.get("action", "ADD").upper()
                    memory = item.get("memory", "")
                    old_id = item.get("old_memory_id")
                    if action in ("ADD", "UPDATE", "NOOP") and memory:
                        result.append({
                            "memory": str(memory)[:200],
                            "action": action,
                            "old_memory_id": str(old_id) if old_id else None,
                        })
                return result
    except asyncio.TimeoutError:
        _log.warning("mem0_dedup_timeout", model=model)
    except Exception as exc:
        _log.warning("mem0_dedup_failed", error=str(exc)[:200])

    # Fallback: if LLM fails, use vector similarity for simple dedup
    # If a new fact is >0.92 similar to an existing one, treat as NOOP; else ADD
    return _vector_dedup_fallback(new_facts, existing_memories)


def _vector_dedup_fallback(
    new_facts: list[str],
    existing_memories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fallback dedup using cosine similarity threshold."""
    SIM_THRESHOLD = 0.92
    result = []
    for fact in new_facts:
        is_dup = False
        for existing in existing_memories:
            # Simple text overlap heuristic when vectors aren't available
            existing_content = existing.get("content", "")
            # Check if one contains the other (simple heuristic)
            if (fact in existing_content or existing_content in fact
                    or _text_similarity(fact, existing_content) > SIM_THRESHOLD):
                is_dup = True
                break
        result.append({
            "memory": fact,
            "action": "NOOP" if is_dup else "ADD",
            "old_memory_id": None,
        })
    return result


def _text_similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity on character bigrams."""
    if not a or not b:
        return 0.0
    set_a = set(a[i:i+2] for i in range(len(a) - 1))
    set_b = set(b[i:i+2] for i in range(len(b) - 1))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


# ── Keyword search via Elasticsearch (hybrid retrieval) ──

async def _keyword_search(
    user_id: str,
    query: str,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Keyword-based memory search using Elasticsearch (BM25).

    Returns list of {memory_id, content, score, metadata}.
    """
    if not settings.es_enabled:
        return []

    try:
        from app.rag.search.keyword import get_es_client

        es = await get_es_client()
        index_name = f"mem0_{settings.mem0_collection_name}"

        # Check if index exists
        exists = await es.indices.exists(index=index_name)
        if not exists:
            return []

        resp = await es.search(
            index=index_name,
            body={
                "query": {
                    "bool": {
                        "must": [{"term": {"user_id": user_id}}],
                        "should": [
                            {"match": {"content": {"query": query, "boost": 2.0}}},
                        ],
                        "minimum_should_match": 1,
                    },
                },
                "size": top_k,
                "_source": ["memory_id", "content", "metadata"],
            },
        )

        results = []
        for hit in resp["hits"]["hits"]:
            score = float(hit.get("_score", 0))
            src = hit.get("_source", {})
            results.append({
                "memory_id": src.get("memory_id", ""),
                "content": src.get("content", ""),
                "score": min(score / 10.0, 1.0),  # normalize BM25 to ~0-1
                "metadata": src.get("metadata", {}),
            })
        return results
    except Exception as exc:
        _log.debug("mem0_keyword_search_skipped", error=str(exc)[:200])
        return []


async def _index_to_es(
    user_id: str,
    tenant_id: str,
    memory_id: str,
    content: str,
    metadata: dict,
) -> None:
    """Index a memory fact into Elasticsearch for keyword search."""
    if not settings.es_enabled:
        return

    try:
        from app.rag.search.keyword import get_es_client

        es = await get_es_client()
        index_name = f"mem0_{settings.mem0_collection_name}"

        # Create index if not exists
        exists = await es.indices.exists(index=index_name)
        if not exists:
            await es.indices.create(
                index=index_name,
                body={
                    "mappings": {
                        "properties": {
                            "memory_id": {"type": "keyword"},
                            "user_id": {"type": "keyword"},
                            "tenant_id": {"type": "keyword"},
                            "content": {
                                "type": "text",
                                "analyzer": settings.es_analyzer,
                                "search_analyzer": settings.es_search_analyzer,
                            },
                            "metadata": {"type": "object", "enabled": False},
                        }
                    }
                },
            )

        await es.index(
            index=index_name,
            id=memory_id,
            document={
                "memory_id": memory_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "content": content,
                "metadata": metadata,
            },
        )
    except Exception as exc:
        _log.debug("mem0_es_index_skipped", error=str(exc)[:200])


async def _delete_from_es(memory_id: str) -> None:
    """Delete a memory fact from Elasticsearch."""
    if not settings.es_enabled:
        return
    try:
        from app.rag.search.keyword import get_es_client

        es = await get_es_client()
        index_name = f"mem0_{settings.mem0_collection_name}"
        await es.delete(index=index_name, id=memory_id, ignore=[404])
    except Exception:
        pass


# ── Score fusion for hybrid search ──

def _fuse_results(
    vector_results: list[dict[str, Any]],
    keyword_results: list[dict[str, Any]],
    top_k: int,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> list[dict[str, Any]]:
    """Fuse vector + keyword results using weighted score combination."""
    all_memories: dict[str, dict[str, Any]] = {}

    for m in vector_results:
        mid = m.get("memory_id", "")
        all_memories[mid] = {
            **m,
            "vector_score": m.get("score", 0),
            "keyword_score": 0,
        }

    for m in keyword_results:
        mid = m.get("memory_id", "")
        if mid in all_memories:
            all_memories[mid]["keyword_score"] = m.get("score", 0)
        else:
            all_memories[mid] = {
                **m,
                "vector_score": 0,
                "keyword_score": m.get("score", 0),
            }

    # Compute fused score
    for m in all_memories.values():
        m["score"] = (
            m["vector_score"] * vector_weight
            + m["keyword_score"] * keyword_weight
        )

    # Sort by fused score and take top_k
    sorted_memories = sorted(
        all_memories.values(), key=lambda x: x["score"], reverse=True
    )
    return sorted_memories[:top_k]


# ── Main manager ──


class Mem0Manager:
    """Manages user memory facts: extraction, dedup, storage, and retrieval.

    Implements the mem0 pattern:
      1. Memory Extraction — LLM extracts facts from conversations
      2. Memory Update & Dedup — LLM compares new vs existing, decides ADD/UPDATE/NOOP
      3. Hybrid Retrieval — vector (Milvus) + keyword (ES) dual-channel with fusion
      4. Auto Injection — caller injects returned memories into generation prompt
    """

    _instance: Mem0Manager | None = None

    @classmethod
    def get_instance(cls) -> Mem0Manager:
        if cls._instance is None:
            cls._instance = Mem0Manager()
        return cls._instance

    def __init__(self):
        self._embedder = None
        self._collection = None

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedding_client()
        return self._embedder

    @property
    def collection(self):
        if self._collection is None:
            try:
                self._collection = _get_collection()
            except Exception as exc:
                _log.error("mem0_collection_init_failed", error=str(exc)[:200])
                return None
        return self._collection

    async def add(
        self,
        user_id: str,
        tenant_id: str,
        messages: list[dict],
    ) -> int:
        """Extract facts from messages, dedup against existing memories, and store.

        Uses LLM to determine ADD (new), UPDATE (supersede old), or NOOP (duplicate).
        For UPDATE, the old memory is deleted before inserting the new one.

        Returns count of facts actually stored (ADDs + UPDATEs).
        """
        if not settings.mem0_enabled:
            return 0

        # Step 1: Extract facts from the conversation
        new_facts = await _extract_facts(messages)
        if not new_facts:
            return 0

        # Step 2: Retrieve existing memories for this user (for dedup)
        existing = await self.get_all(user_id, limit=200)

        # Step 3: LLM-based conflict resolution (ADD / UPDATE / NOOP)
        decisions = await _resolve_conflicts(user_id, new_facts, existing)

        if not decisions:
            return 0

        # Step 4: Process decisions
        to_add: list[str] = []
        to_delete_ids: list[str] = []

        for decision in decisions:
            action = decision.get("action", "ADD").upper()
            memory = decision.get("memory", "")
            old_id = decision.get("old_memory_id")

            if action == "NOOP":
                continue
            elif action == "UPDATE" and old_id:
                to_delete_ids.append(old_id)
                to_add.append(memory)
            elif action == "ADD":
                to_add.append(memory)

        if not to_add:
            _log.info("mem0_all_noop", user_id=user_id, new_facts=len(new_facts))
            return 0

        # Step 5: Delete superseded memories (UPDATE targets)
        # Replacements are inserted before superseded facts are retired.
        try:
            vectors = await self.embedder.embed_documents(to_add)
        except Exception as exc:
            _log.error("mem0_embed_failed", error=str(exc)[:200])
            return 0

        col = self.collection
        if col is None:
            return 0

        # Auto-heal: detect embedding dimension mismatch and recreate collection
        if vectors:
            actual_dim = len(vectors[0])
            schema_dim = _get_collection_dim(col)
            if schema_dim is not None and schema_dim != actual_dim:
                _log.warning(
                    "mem0_dim_mismatch_auto_heal",
                    schema_dim=schema_dim,
                    actual_dim=actual_dim,
                )
                raise EmbeddingDimensionMismatch(
                    settings.mem0_collection_name, schema_dim, actual_dim
                )

        now = int(time.time())
        records = []
        memory_ids = []
        for i, (fact, vec) in enumerate(zip(to_add, vectors)):
            memory_id = uuid.uuid4().hex[:16]
            memory_ids.append(memory_id)
            metadata = {"source": "conversation", "ts": now}
            records.append({
                "pk": f"{user_id}_{now}_{i}",
                "memory_id": memory_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "content": fact[:8192],
                "metadata": metadata,
                "embedding": vec,
            })

        try:
            col.insert(records)
            col.flush()

            # Also index into ES for keyword search
            for mid, fact, metadata in zip(memory_ids, to_add, [r["metadata"] for r in records]):
                await _index_to_es(user_id, tenant_id, mid, fact, metadata)

            for old_id in to_delete_ids:
                await self._delete_by_memory_id(old_id)

            _log.info(
                "mem0_facts_stored",
                user_id=user_id,
                added=len(to_add),
                updated=len(to_delete_ids),
                noop=len(decisions) - len(to_add),
            )
            return len(to_add)
        except Exception as exc:
            _log.error("mem0_insert_failed", error=str(exc)[:200])
            return 0

    async def search(
        self,
        user_id: str,
        query: str,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search: vector (Milvus) + keyword (ES) with score fusion.

        Returns list of {memory_id, content, score, metadata}.
        """
        if not settings.mem0_enabled:
            return []

        col = self.collection
        if col is None:
            return []

        top_k = top_k or settings.mem0_search_top_k

        # Channel 1: Vector search via Milvus
        vector_results: list[dict[str, Any]] = []
        try:
            query_vec = await self.embedder.embed_query(query)

            # Auto-heal: detect embedding dimension mismatch and recreate collection
            actual_dim = len(query_vec)
            schema_dim = _get_collection_dim(col)
            if schema_dim is not None and schema_dim != actual_dim:
                _log.warning(
                    "mem0_dim_mismatch_auto_heal",
                    schema_dim=schema_dim,
                    actual_dim=actual_dim,
                )
                raise EmbeddingDimensionMismatch(
                    settings.mem0_collection_name, schema_dim, actual_dim
                )

            results = col.search(
                data=[query_vec],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                limit=top_k * 2,
                expr=f'user_id == "{user_id}"',
                output_fields=["memory_id", "content", "metadata"],
            )
            for hits in results:
                for hit in hits:
                    vector_results.append({
                        "memory_id": hit.entity.get("memory_id", ""),
                        "content": hit.entity.get("content", ""),
                        "score": float(hit.score),
                        "metadata": hit.entity.get("metadata", {}),
                    })
        except Exception as exc:
            _log.warning("mem0_vector_search_failed", error=str(exc)[:200])

        # Channel 2: Keyword search via ES (if enabled)
        keyword_results = await _keyword_search(user_id, query, top_k=top_k * 2)

        # Fuse results
        if keyword_results:
            memories = _fuse_results(vector_results, keyword_results, top_k)
        else:
            # Vector-only fallback
            memories = sorted(vector_results, key=lambda x: x["score"], reverse=True)[:top_k]

        _log.debug(
            "mem0_hybrid_search_done",
            user_id=user_id,
            vector_hits=len(vector_results),
            keyword_hits=len(keyword_results),
            fused=len(memories),
        )
        return [
            memory
            for memory in memories
            if float(memory.get("score", 0.0))
            >= settings.mem0_min_relevance_score
        ]

    async def get_all(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get all memory facts for a user (for admin detail view)."""
        if not settings.mem0_enabled:
            return []

        col = self.collection
        if col is None:
            return []

        try:
            results = col.query(
                expr=f'user_id == "{user_id}"',
                output_fields=["memory_id", "content", "metadata"],
                limit=limit,
            )
            return [
                {
                    "memory_id": r.get("memory_id", ""),
                    "content": r.get("content", ""),
                    "metadata": r.get("metadata", {}),
                }
                for r in results
            ]
        except Exception as exc:
            _log.warning("mem0_get_all_failed", error=str(exc)[:200])
            return []

    async def _delete_by_memory_id(self, memory_id: str) -> bool:
        """Delete a single memory fact by memory_id (internal, used by UPDATE)."""
        col = self.collection
        if col is None:
            return False
        try:
            col.delete(expr=f'memory_id == "{memory_id}"')
            col.flush()
            await _delete_from_es(memory_id)
            _log.info("mem0_fact_deleted", memory_id=memory_id, reason="update_supersede")
            return True
        except Exception as exc:
            _log.warning("mem0_delete_failed", error=str(exc)[:200])
            return False

    async def delete(self, memory_id: str) -> bool:
        """Delete a single memory fact by memory_id (public, for admin)."""
        return await self._delete_by_memory_id(memory_id)

    async def count(self, user_id: str) -> int:
        """Count memory facts for a user."""
        col = self.collection
        if col is None:
            return 0
        try:
            results = col.query(
                expr=f'user_id == "{user_id}"',
                output_fields=["memory_id"],
                limit=16384,
            )
            return len(results)
        except Exception:
            return 0
