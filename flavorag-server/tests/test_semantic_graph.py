"""Tests for evidence-grounded, incremental semantic graph extraction."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _chunks():
    return [
        {
            "chunk_id": "chunk-1",
            "doc_id": "doc-1",
            "tenant_id": "tenant-1",
            "content": (
                "GraphRAG uses Neo4j to store its knowledge graph. "
                "The endpoint accepts JSON through an API."
            ),
        }
    ]


def test_semantic_backfill_groups_existing_chunks_without_rechunking():
    from app.rag.graph.semantic_backfill import _group_rows

    rows = [
        SimpleNamespace(
            kb_id="kb-1",
            doc_id="doc-1",
            collection_name="collection-1",
            active_collection_name="collection-1-v2",
            chunk_id="chunk-1",
            tenant_id="tenant-1",
            content="first",
        ),
        SimpleNamespace(
            kb_id="kb-1",
            doc_id="doc-1",
            collection_name="collection-1",
            active_collection_name="collection-1-v2",
            chunk_id="chunk-2",
            tenant_id="tenant-1",
            content="second",
        ),
    ]

    documents = _group_rows(rows)

    assert documents == [
        {
            "kb_id": "kb-1",
            "doc_id": "doc-1",
            "collection_name": "collection-1-v2",
            "chunks": [
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "doc-1",
                    "tenant_id": "tenant-1",
                    "content": "first",
                },
                {
                    "chunk_id": "chunk-2",
                    "doc_id": "doc-1",
                    "tenant_id": "tenant-1",
                    "content": "second",
                },
            ],
        }
    ]


def test_semantic_prompt_batches_bound_chunks_and_keep_every_chunk():
    from app.rag.graph.semantic_extractor import _prompt_batches

    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "doc_id": "doc-1",
            "tenant_id": "tenant-1",
            "content": "x" * 20,
        }
        for index in range(5)
    ]

    batches = _prompt_batches(chunks, max_chars=45, max_chunks=2)

    assert [len(batch) for _prompt, batch in batches] == [2, 2, 1]
    assert [
        chunk["chunk_id"]
        for _prompt, batch in batches
        for chunk in batch
    ] == [f"chunk-{index}" for index in range(5)]


def test_semantic_prompt_uses_configured_candidate_limits(monkeypatch):
    from app.config.settings import settings
    from app.rag.graph.semantic_extractor import _system_prompt

    monkeypatch.setattr(settings, "graph_semantic_max_entities_per_batch", 7)
    monkeypatch.setattr(
        settings,
        "graph_semantic_max_relationships_per_batch",
        9,
    )

    prompt = _system_prompt()

    assert "最多输出 7 个实体、9 条关系" in prompt


def test_semantic_evidence_strictness_can_be_configured():
    from app.rag.graph.semantic_extractor import validate_extraction

    payload = {
        "entities": [
            {"name": "ServiceA", "type": "service", "chunk_id": "chunk-1"},
            {"name": "Kafka", "type": "technology", "chunk_id": "chunk-1"},
        ],
        "relationships": [
            {
                "source": "ServiceA",
                "target": "Kafka",
                "type": "USES",
                "confidence": 0.9,
                "evidence": "Kafka is used for events.",
                "chunk_id": "chunk-1",
            }
        ],
    }
    chunks = [
        {
            "chunk_id": "chunk-1",
            "content": "ServiceA architecture. Kafka is used for events.",
        }
    ]

    strict = validate_extraction(
        payload,
        chunks=chunks,
        min_confidence=0.7,
        require_endpoints_in_evidence=True,
    )
    relaxed = validate_extraction(
        payload,
        chunks=chunks,
        min_confidence=0.7,
        require_endpoints_in_evidence=False,
    )

    assert strict["relationships"] == []
    assert len(relaxed["relationships"]) == 1


def test_semantic_validation_keeps_only_grounded_confident_relations():
    from app.rag.graph.semantic_extractor import validate_extraction

    raw = """```json
    {
      "entities": [
        {"name":"GraphRAG","type":"system","description":"图检索系统","chunk_id":"chunk-1"},
        {"name":"Neo4j","type":"technology","description":"图数据库","chunk_id":"chunk-1"},
        {"name":"JSON","type":"data","description":"数据格式","chunk_id":"chunk-1"},
        {"name":"API","type":"api","description":"接口","chunk_id":"chunk-1"},
        {"name":"PostgreSQL","type":"technology","description":"臆测实体","chunk_id":"chunk-1"}
      ],
      "relationships": [
        {"source":"GraphRAG","target":"Neo4j","type":"STORES_IN",
         "description":"使用图数据库","confidence":0.94,
         "evidence":"GraphRAG uses Neo4j to store its knowledge graph.","chunk_id":"chunk-1"},
        {"source":"JSON","target":"API","type":"PART_OF",
         "description":"低置信弱关系","confidence":0.40,
         "evidence":"The endpoint accepts JSON through an API.","chunk_id":"chunk-1"},
        {"source":"GraphRAG","target":"Neo4j","type":"DEPENDS_ON",
         "description":"证据是模型编的","confidence":0.99,
         "evidence":"GraphRAG cannot run without Neo4j.","chunk_id":"chunk-1"}
      ]
    }
    ```"""

    result = validate_extraction(raw, chunks=_chunks(), min_confidence=0.70)

    assert [item["name"] for item in result["entities"]] == [
        "GraphRAG",
        "Neo4j",
        "JSON",
        "API",
    ]
    assert result["relationships"] == [
        {
            "source": "GraphRAG",
            "target": "Neo4j",
            "type": "STORES_IN",
            "description": "使用图数据库",
            "confidence": 0.94,
            "evidence": "GraphRAG uses Neo4j to store its knowledge graph.",
            "chunk_id": "chunk-1",
        }
    ]
    assert result["rejected"] == 3


def test_generic_transport_terms_never_form_name_only_cross_kb_bridges():
    from app.rag.graph.neo4j_store import Neo4jGraphStore

    for name in ("JSON", "API", "HTTP", "HTTPS", "XML", "YAML"):
        assert Neo4jGraphStore._is_cross_kb_candidate(name) is False
    assert Neo4jGraphStore._is_cross_kb_candidate("GraphRAG") is True
    assert Neo4jGraphStore._is_cross_kb_candidate("Neo4j") is True


def test_relation_evidence_must_name_both_ends_and_support_direction():
    from app.rag.graph.semantic_extractor import validate_extraction

    chunks = [
        {
            "chunk_id": "chunk-1",
            "content": (
                "src/session/store.ts 会删除 secret。"
                "flavor-code 的 Harness 设计已经成熟。"
                "Harness PART_OF flavor-code。"
            ),
        }
    ]
    entities = [
        {"name": name, "type": "concept", "chunk_id": "chunk-1"}
        for name in ("src/session/store.ts", "secret", "flavor-code", "Harness")
    ]
    result = validate_extraction(
        {
            "entities": entities,
            "relationships": [
                {
                    "source": "src/session/store.ts",
                    "target": "secret",
                    "type": "STORES_IN",
                    "confidence": 0.99,
                    "evidence": "会删除 secret",
                    "chunk_id": "chunk-1",
                },
                {
                    "source": "flavor-code",
                    "target": "Harness",
                    "type": "PART_OF",
                    "confidence": 0.98,
                    "evidence": "flavor-code 的 Harness 设计已经成熟。",
                    "chunk_id": "chunk-1",
                },
                {
                    "source": "Harness",
                    "target": "flavor-code",
                    "type": "PART_OF",
                    "confidence": 0.95,
                    "evidence": "Harness PART_OF flavor-code。",
                    "chunk_id": "chunk-1",
                },
            ],
        },
        chunks=chunks,
        min_confidence=0.70,
    )

    assert [
        (item["source"], item["type"], item["target"])
        for item in result["relationships"]
    ] == [("Harness", "PART_OF", "flavor-code")]
    assert result["rejected"] == 2


@pytest.mark.asyncio
async def test_extractor_uses_zero_temperature_and_persists_validated_output(
    monkeypatch,
):
    from app.config.settings import settings
    from app.rag.graph.semantic_extractor import extract_and_store_semantic_graph

    monkeypatch.setattr(settings, "graph_semantic_enabled", True)
    monkeypatch.setattr(settings, "graph_semantic_min_confidence", 0.70)
    monkeypatch.setattr(settings, "graph_semantic_temperature", 0.25)
    monkeypatch.setattr(settings, "graph_semantic_max_tokens", 777)
    calls = {}

    class FakeLLM:
        async def chat_stream(self, messages, temperature, max_tokens):
            calls["messages"] = messages
            calls["temperature"] = temperature
            calls["max_tokens"] = max_tokens
            output = {
                "entities": [
                    {
                        "name": "GraphRAG",
                        "type": "system",
                        "description": "图检索系统",
                        "chunk_id": "chunk-1",
                    },
                    {
                        "name": "Neo4j",
                        "type": "technology",
                        "description": "图数据库",
                        "chunk_id": "chunk-1",
                    },
                ],
                "relationships": [
                    {
                        "source": "GraphRAG",
                        "target": "Neo4j",
                        "type": "STORES_IN",
                        "description": "存储图数据",
                        "confidence": 0.91,
                        "evidence": "GraphRAG uses Neo4j to store its knowledge graph.",
                        "chunk_id": "chunk-1",
                    }
                ],
            }
            for part in (json.dumps(output)[:40], json.dumps(output)[40:]):
                yield part

    class FakeStore:
        async def upsert_semantic_graph(self, **kwargs):
            calls["store"] = kwargs
            return {"nodes": len(kwargs["extraction"]["entities"]), "edges": 1}

    result = await extract_and_store_semantic_graph(
        kb_id="kb-1",
        collection_name="collection-1",
        chunks=_chunks(),
        llm_client=FakeLLM(),
        store=FakeStore(),
    )

    assert calls["temperature"] == 0.25
    assert calls["max_tokens"] == 777
    assert "[chunk_id=chunk-1]" in calls["messages"][1]["content"]
    assert calls["store"]["model"] == "injected-test-model"
    assert result == {
        "status": "complete",
        "entities": 2,
        "edges": 1,
        "rejected": 0,
    }


@pytest.mark.asyncio
async def test_extractor_falls_back_to_a_compatible_lightweight_provider(
    monkeypatch,
):
    import app.rag.graph.semantic_extractor as module
    from app.config.settings import settings

    monkeypatch.setattr(settings, "graph_semantic_enabled", True)
    monkeypatch.setattr(settings, "graph_semantic_api_key", "")
    monkeypatch.setattr(settings, "bailian_api_key", "bad-bailian")
    monkeypatch.setattr(settings, "llm_base_url", "https://bailian.example/v1")
    monkeypatch.setattr(settings, "graph_semantic_model", "qwen-turbo")
    monkeypatch.setattr(settings, "hyde_api_key", "working-deepseek")
    monkeypatch.setattr(settings, "hyde_base_url", "https://deepseek.example/v1")
    monkeypatch.setattr(settings, "hyde_model", "deepseek-flash")
    monkeypatch.setattr(settings, "mem0_api_key", "")
    monkeypatch.setattr(settings, "reasoning_api_key", "")
    created: list[tuple[str, str]] = []

    output = json.dumps(
        {
            "entities": [
                {
                    "name": "GraphRAG",
                    "type": "system",
                    "description": "",
                    "chunk_id": "chunk-1",
                },
                {
                    "name": "Neo4j",
                    "type": "technology",
                    "description": "",
                    "chunk_id": "chunk-1",
                },
            ],
            "relationships": [
                {
                    "source": "GraphRAG",
                    "target": "Neo4j",
                    "type": "STORES_IN",
                    "description": "",
                    "confidence": 0.9,
                    "evidence": "GraphRAG uses Neo4j to store its knowledge graph.",
                    "chunk_id": "chunk-1",
                }
            ],
        }
    )

    class FakeClient:
        def __init__(self, *, api_key, base_url, model):
            self.api_key = api_key
            self.base_url = base_url
            self.model = model
            created.append((base_url, model))

        async def chat_stream(self, *_args, **_kwargs):
            if "bailian" in self.base_url:
                raise PermissionError("403")
            yield output

    class FakeStore:
        async def upsert_semantic_graph(self, **kwargs):
            assert kwargs["model"] == "deepseek-flash"
            return {"nodes": 2, "edges": 1}

    monkeypatch.setattr(module, "LLMClient", FakeClient)
    result = await module.extract_and_store_semantic_graph(
        kb_id="kb-1",
        collection_name="collection-1",
        chunks=_chunks(),
        store=FakeStore(),
    )

    assert created[:2] == [
        ("https://bailian.example/v1", "qwen-turbo"),
        ("https://deepseek.example/v1", "deepseek-flash"),
    ]
    assert result["status"] == "complete"


@pytest.mark.asyncio
async def test_semantic_store_replaces_old_edges_and_keeps_provenance(monkeypatch):
    from app.rag.graph.neo4j_store import Neo4jGraphStore

    calls: list[tuple[str, dict]] = []

    class Result:
        async def data(self):
            return []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def run(self, query, **kwargs):
            calls.append((query, kwargs))
            return Result()

    class Driver:
        def session(self):
            return Session()

    store = Neo4jGraphStore()
    monkeypatch.setattr(store, "_driver", lambda: Driver())
    extraction = {
        "entities": [
            {
                "name": "GraphRAG",
                "type": "system",
                "description": "图检索系统",
                "chunk_id": "chunk-1",
            },
            {
                "name": "Neo4j",
                "type": "technology",
                "description": "图数据库",
                "chunk_id": "chunk-1",
            },
        ],
        "relationships": [
            {
                "source": "GraphRAG",
                "target": "Neo4j",
                "type": "STORES_IN",
                "description": "存储图数据",
                "confidence": 0.91,
                "evidence": "GraphRAG uses Neo4j to store its knowledge graph.",
                "chunk_id": "chunk-1",
            }
        ],
    }

    result = await store.upsert_semantic_graph(
        kb_id="kb-1",
        collection_name="collection-1",
        chunks=_chunks(),
        extraction=extraction,
        model="qwen-turbo-latest",
        prompt_version="v1",
    )

    delete_query = next(
        query for query, _ in calls if "DELETE relation" in query
    )
    entity_query = next(
        query for query, _args in calls if "ON CREATE SET" in query
    )
    relation_query, relation_args = next(
        (query, args)
        for query, args in calls
        if "MERGE (source)-[relation:SEMANTIC_RELATED" in query
    )
    assert "doc_id: $doc_id" in delete_query
    assert "ON CREATE SET entity.deterministic_extracted = false" in entity_query
    assert "coalesce(entity.deterministic_extracted, true)" in entity_query
    assert "evidence_id: row.evidence_id" in relation_query
    relation = relation_args["rows"][0]
    assert relation["confidence"] == 0.91
    assert relation["evidence"].startswith("GraphRAG uses Neo4j")
    assert relation["model"] == "qwen-turbo-latest"
    assert relation["prompt_version"] == "v1"
    assert result == {"nodes": 2, "edges": 1}


@pytest.mark.asyncio
async def test_graph_response_exposes_semantic_relation_evidence(monkeypatch):
    from app.rag.graph.neo4j_store import Neo4jGraphStore

    nodes = [
        {
            "id": "source",
            "name": "GraphRAG",
            "type": "system",
            "description": "",
            "document_id": "doc-1",
            "knowledge_base_id": "kb-1",
        },
        {
            "id": "target",
            "name": "Neo4j",
            "type": "technology",
            "description": "",
            "document_id": "doc-1",
            "knowledge_base_id": "kb-1",
        },
    ]
    edges = [
        {
            "source": "source",
            "target": "target",
            "chunk_id": "chunk-1",
            "label": "STORES_IN",
            "relation_type": "STORES_IN",
            "description": "存储图数据",
            "confidence": 0.91,
            "evidence": "GraphRAG uses Neo4j to store its knowledge graph.",
            "model": "qwen-turbo-latest",
            "cross_kb": False,
        }
    ]

    class Result:
        def __init__(self, rows):
            self.rows = rows

        async def data(self):
            return self.rows

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def run(self, query, **_kwargs):
            if "RETURN source.id AS source" in query:
                assert "SEMANTIC_RELATED" in query
                return Result(edges)
            assert "SEMANTIC_RELATED" in query
            assert "semantic_rank DESC" in query
            return Result(nodes)

    class Driver:
        def session(self):
            return Session()

    store = Neo4jGraphStore()
    monkeypatch.setattr(store, "_driver", lambda: Driver())
    graph = await store.fetch_graph(kb_id="kb-1", limit=10)

    assert graph["edges"][0] == {
        "id": "source:target:STORES_IN:chunk-1",
        "source": "source",
        "target": "target",
        "label": "STORES_IN",
        "description": "存储图数据",
        "type": "STORES_IN",
        "confidence": 0.91,
        "evidence": "GraphRAG uses Neo4j to store its knowledge graph.",
        "model": "qwen-turbo-latest",
        "crossKnowledgeBase": False,
    }


@pytest.mark.asyncio
async def test_ingestion_keeps_native_graph_and_requests_repair_when_semantic_fails(
    monkeypatch,
):
    import app.ingestion.pipeline as pipeline_module
    import app.rag.graph.lightrag_client as lightrag_module
    import app.rag.graph.neo4j_store as neo4j_module
    import app.rag.graph.semantic_extractor as semantic_module
    from app.ingestion.pipeline import IngestionPipeline

    calls: list[str] = []

    class QueryResult:
        def first(self):
            return ("collection-1",)

    class Session:
        async def execute(self, _query):
            return QueryResult()

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return None

    class NativeStore:
        async def upsert_chunks(self, **_kwargs):
            calls.append("native")
            return {"nodes": 2, "edges": 1, "crossEdges": None}

    class LightRAG:
        async def insert_documents_batch(self, *_args, **_kwargs):
            calls.append("lightrag")

    async def fail_semantic(**_kwargs):
        calls.append("semantic")
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        pipeline_module, "async_session_factory", lambda: SessionContext()
    )
    monkeypatch.setattr(neo4j_module, "Neo4jGraphStore", NativeStore)
    monkeypatch.setattr(lightrag_module, "LightRAGClient", LightRAG)
    monkeypatch.setattr(
        semantic_module, "extract_and_store_semantic_graph", fail_semantic
    )
    pipeline = IngestionPipeline.__new__(IngestionPipeline)
    chunks = [
        SimpleNamespace(
            id="chunk-1",
            doc_id="doc-1",
            tenant_id="tenant-1",
            content="GraphRAG uses Neo4j.",
        )
    ]

    assert await pipeline._sync_to_lightrag("kb-1", chunks) is False
    assert calls == ["native", "semantic", "lightrag"]


@pytest.mark.asyncio
async def test_document_delete_detaches_semantic_nodes_and_rebuilds_bridges(
    monkeypatch,
):
    from app.rag.graph.neo4j_store import Neo4jGraphStore

    calls: list[tuple[str, dict]] = []

    class Result:
        def __init__(self, rows=None):
            self.rows = rows or []

        async def data(self):
            return self.rows

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def run(self, query, **kwargs):
            calls.append((query, kwargs))
            if "RETURN DISTINCT node.tenant_id" in query:
                return Result(
                    [
                        {
                            "tenant_id": "tenant-1",
                            "normalized_name": "graphrag",
                        }
                    ]
                )
            return Result()

    class Driver:
        def session(self):
            return Session()

    store = Neo4jGraphStore()
    monkeypatch.setattr(store, "_driver", lambda: Driver())
    await store.delete_document(kb_id="kb-1", doc_id="doc-1")

    assert any(
        "DETACH DELETE n" in query
        and args == {"kb_id": "kb-1", "doc_id": "doc-1"}
        for query, args in calls
    )
    assert any(
        "MERGE (source)-[relation:CROSS_KB_RELATED]" in query
        for query, _args in calls
    )


@pytest.mark.asyncio
async def test_graph_repair_retries_native_semantic_and_lightrag(monkeypatch):
    import app.rag.graph.lightrag_client as lightrag_module
    import app.rag.graph.neo4j_store as neo4j_module
    import app.rag.graph.semantic_extractor as semantic_module
    from app.services.index_repair import repair_graph_document

    calls: list[str] = []

    class Native:
        async def upsert_chunks(self, **_kwargs):
            calls.append("native")

    class LightRAG:
        async def insert_documents_batch(self, *_args, **_kwargs):
            calls.append("lightrag")

    async def semantic(**_kwargs):
        calls.append("semantic")

    monkeypatch.setattr(neo4j_module, "Neo4jGraphStore", Native)
    monkeypatch.setattr(lightrag_module, "LightRAGClient", LightRAG)
    monkeypatch.setattr(
        semantic_module, "extract_and_store_semantic_graph", semantic
    )
    kb = SimpleNamespace(
        id="kb-1",
        collection_name="collection-1",
        active_collection_name="collection-1-v2",
    )
    chunks = [
        SimpleNamespace(
            id="chunk-1",
            doc_id="doc-1",
            tenant_id="tenant-1",
            content="GraphRAG uses Neo4j.",
        )
    ]

    await repair_graph_document(kb, chunks)

    assert calls == ["native", "semantic", "lightrag"]
