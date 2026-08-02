"""Contract tests for permission-safe cross-knowledge-base Graph RAG."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rag.search.base import SearchResult


@pytest.mark.asyncio
async def test_global_scope_resolves_only_readable_knowledge_bases(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.chat import resolve_chat_kb_scopes
    from app.database.sqlite_schema import initialize_sqlite_schema
    from app.models import KnowledgeBase

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scopes.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await initialize_sqlite_schema(engine)
        async with sessions() as session:
            session.add_all(
                [
                    KnowledgeBase(
                        id="kb-a",
                        name="A",
                        embedding_model="embed-a",
                        collection_name="collection-a",
                        tenant_id="tenant-a",
                        visibility="TENANT",
                        created_by="owner",
                    ),
                    KnowledgeBase(
                        id="kb-b",
                        name="B",
                        embedding_model="embed-b",
                        collection_name="collection-b",
                        active_collection_name="collection-b-v2",
                        tenant_id="tenant-a",
                        visibility="TENANT",
                        created_by="owner",
                    ),
                    KnowledgeBase(
                        id="kb-secret",
                        name="Secret",
                        embedding_model="embed-secret",
                        collection_name="collection-secret",
                        tenant_id="tenant-b",
                        visibility="TENANT",
                        created_by="owner",
                    ),
                ]
            )
            await session.commit()

        user = SimpleNamespace(
            id="reader",
            tenant_id="tenant-a",
            department_id="",
            role="user",
        )
        async with sessions() as session:
            scopes = await resolve_chat_kb_scopes(session, user, "*")

        assert [scope.kb_id for scope in scopes] == ["kb-a", "kb-b"]
        assert [scope.collection_name for scope in scopes] == [
            "collection-a",
            "collection-b-v2",
        ]
        assert [scope.embedding_model for scope in scopes] == ["embed-a", "embed-b"]
    finally:
        await engine.dispose()


def test_global_scope_forces_graph_rag():
    from app.api.chat import effective_graph_rag

    assert effective_graph_rag("*", False, server_default=False) is True
    assert effective_graph_rag("kb-a", False, server_default=True) is False
    assert effective_graph_rag("kb-a", None, server_default=True) is True


def test_retrieval_unavailable_has_a_retryable_refusal_message():
    from app.api.chat import retrieval_refusal_message

    unavailable = retrieval_refusal_message("retrieval_unavailable")
    knowledge_gap = retrieval_refusal_message("insufficient_relevance")

    assert "暂时不可用" in unavailable
    assert "稍后重试" in unavailable
    assert "没有找到足够可靠的资料" in knowledge_gap


@pytest.mark.asyncio
async def test_acl_filter_accepts_only_exact_resolved_kb_set(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database.sqlite_schema import initialize_sqlite_schema
    from app.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
    from app.security.access import Principal
    from app.security.service import filter_authorized_results

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'acl-set.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await initialize_sqlite_schema(engine)
        async with sessions() as session:
            for suffix in ("a", "b", "outside"):
                kb_id = f"kb-{suffix}"
                doc_id = f"doc-{suffix}"
                session.add(
                    KnowledgeBase(
                        id=kb_id,
                        name=suffix,
                        embedding_model="model",
                        collection_name=f"collection-{suffix}",
                        tenant_id="tenant-a",
                        visibility="TENANT",
                        created_by="owner",
                    )
                )
                session.add(
                    KnowledgeDocument(
                        id=doc_id,
                        kb_id=kb_id,
                        tenant_id="tenant-a",
                        visibility="INHERIT",
                        doc_name=f"{suffix}.txt",
                        file_url=f"{suffix}.txt",
                        file_type="txt",
                        created_by="owner",
                    )
                )
                session.add(
                    KnowledgeChunk(
                        id=f"chunk-{suffix}",
                        kb_id=kb_id,
                        doc_id=doc_id,
                        tenant_id="tenant-a",
                        chunk_index=0,
                        content=suffix,
                        enabled=1,
                        created_by="owner",
                    )
                )
            await session.commit()

        candidates = [
            SearchResult(
                chunk_id=f"chunk-{suffix}",
                doc_id=f"doc-{suffix}",
                content=suffix,
                score=1.0,
            )
            for suffix in ("a", "b", "outside")
        ]
        async with sessions() as session:
            filtered = await filter_authorized_results(
                session,
                Principal("reader", "tenant-a", "", "user"),
                candidates,
                kb_ids=["kb-a", "kb-b"],
            )

        assert [item.chunk_id for item in filtered] == ["chunk-a", "chunk-b"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_vector_search_fans_out_over_queries_and_scopes():
    from app.rag.pipeline import RAGPipeline, RetrievalScope

    pipeline = RAGPipeline.__new__(RAGPipeline)
    calls: list[tuple[str, str, str | None]] = []

    async def fake_search(query, collection_name, *, top_k, embedding_model):
        calls.append((query, collection_name, embedding_model))
        return [SearchResult(f"{query}-{collection_name}", query, 1.0)]

    pipeline._search_vector = fake_search
    scopes = [
        RetrievalScope("kb-a", "A", "collection-a", "embed-a"),
        RetrievalScope("kb-b", "B", "collection-b", "embed-b"),
    ]

    results = await pipeline._search_vector_scopes(
        ["q1", "q2"],
        scopes,
        top_k=5,
    )

    assert len(results) == 4
    assert calls == [
        ("q1", "collection-a", "embed-a"),
        ("q1", "collection-b", "embed-b"),
        ("q2", "collection-a", "embed-a"),
        ("q2", "collection-b", "embed-b"),
    ]


def test_named_global_scopes_are_narrowed_without_expanding_authorization():
    from app.rag.pipeline import RetrievalScope, select_query_scopes

    scopes = [
        RetrievalScope("kb-code", "flavor-code", "collection-code", "embed"),
        RetrievalScope("kb-rag", "flavor-rag", "collection-rag", "embed"),
        RetrievalScope("kb-agent", "huamulan-agent", "collection-agent", "embed"),
        RetrievalScope("kb-short", "AI", "collection-short", "embed"),
    ]

    selected = select_query_scopes(
        "flavor-code 和 FLAVOR-RAG 有哪些可以结合的点？",
        scopes,
    )

    assert [scope.kb_id for scope in selected] == ["kb-code", "kb-rag"]
    assert select_query_scopes("介绍一下检索系统", scopes) == scopes
    assert select_query_scopes("AI 有什么能力？", scopes) == scopes


def test_entity_normalization_is_stable_and_unicode_aware():
    from app.rag.graph.neo4j_store import Neo4jGraphStore

    assert Neo4jGraphStore._normalized_name(" Graph-RAG / 图 谱 ") == "graphrag图谱"
    assert Neo4jGraphStore._is_cross_kb_candidate("Graph RAG") is True
    assert Neo4jGraphStore._is_cross_kb_candidate("Embedding") is True
    assert Neo4jGraphStore._is_cross_kb_candidate("TABLE") is False
    assert Neo4jGraphStore._is_cross_kb_candidate("IF") is False


@pytest.mark.asyncio
async def test_cross_kb_edges_are_tenant_scoped_and_keep_canonical_chunk_id(
    monkeypatch,
):
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
    await store.upsert_chunks(
        kb_id="kb-a",
        collection_name="collection-a",
        chunks=[
            {
                "chunk_id": "chunk-a",
                "doc_id": "doc-a",
                "tenant_id": "tenant-a",
                "content": "# Graph RAG",
            }
        ],
    )

    entity_write = next(
        kwargs for query, kwargs in calls if "SET entity.name" in query
    )
    cross_write = next(
        query
        for query, _kwargs in calls
        if "MERGE (source)-[relation:CROSS_KB_RELATED]" in query
    )
    assert entity_write["rows"][0]["chunk_id"] == "chunk-a"
    assert entity_write["rows"][0]["tenant_id"] == "tenant-a"
    assert entity_write["rows"][0]["cross_linkable"] is True
    assert "candidate.cross_linkable = true" in cross_write
    assert "head(collect(candidate)) AS representative" in cross_write


@pytest.mark.asyncio
async def test_legacy_graph_backfill_plans_one_bridge_per_kb_pair(monkeypatch):
    from app.rag.graph.neo4j_store import Neo4jGraphStore

    graph_nodes = [
        {
            "id": "a-low",
            "name": "Graph RAG",
            "kb_id": "kb-a",
            "tenant_id": None,
            "normalized_name": None,
            "cross_linkable": None,
            "local_degree": 1,
        },
        {
            "id": "a-high",
            "name": "Graph-RAG",
            "kb_id": "kb-a",
            "tenant_id": None,
            "normalized_name": None,
            "cross_linkable": None,
            "local_degree": 8,
        },
        {
            "id": "b",
            "name": "graph_rag",
            "kb_id": "kb-b",
            "tenant_id": None,
            "normalized_name": None,
            "cross_linkable": None,
            "local_degree": 3,
        },
        {
            "id": "noise-a",
            "name": "TABLE",
            "kb_id": "kb-a",
            "tenant_id": None,
            "normalized_name": None,
            "cross_linkable": None,
            "local_degree": 10,
        },
        {
            "id": "noise-b",
            "name": "Table",
            "kb_id": "kb-b",
            "tenant_id": None,
            "normalized_name": None,
            "cross_linkable": None,
            "local_degree": 10,
        },
    ]

    class Result:
        async def data(self):
            return graph_nodes

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def run(self, _query, **_kwargs):
            return Result()

    class Driver:
        def session(self):
            return Session()

    store = Neo4jGraphStore()
    monkeypatch.setattr(store, "_driver", lambda: Driver())

    summary = await store.backfill_cross_kb_relations(
        kb_tenants={"kb-a": "tenant-a", "kb-b": "tenant-a"},
        apply=False,
    )

    assert summary == {
        "knowledgeBases": 2,
        "nodesScanned": 5,
        "nodesUpdated": 5,
        "sharedNames": 1,
        "crossEdges": 1,
        "applied": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_count", "expected_count", "truncated"),
    [(200, 200, False), (201, 200, True)],
)
async def test_combined_graph_caps_at_200_and_reports_true_truncation(
    monkeypatch, node_count, expected_count, truncated
):
    from app.rag.graph.neo4j_store import Neo4jGraphStore

    rows = [
        {
            "id": f"node-{index}",
            "name": f"Node {index}",
            "type": "concept",
            "description": "",
            "document_id": f"doc-{index}",
            "knowledge_base_id": "kb-a" if index % 2 == 0 else "kb-b",
        }
        for index in range(node_count)
    ]

    class Result:
        def __init__(self, data):
            self._data = data

        async def data(self):
            return self._data

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def run(self, query, **_kwargs):
            return Result([] if "RETURN source.id AS source" in query else rows)

    class Driver:
        def session(self):
            return Session()

    store = Neo4jGraphStore()
    monkeypatch.setattr(store, "_driver", lambda: Driver())

    graph = await store.fetch_graph(
        kb_ids=["kb-a", "kb-b"],
        entity="*",
        limit=200,
    )

    assert len(graph["nodes"]) == expected_count
    assert graph["truncated"] is truncated
    assert {node["knowledgeBaseId"] for node in graph["nodes"]} == {"kb-a", "kb-b"}


def test_global_graph_limit_is_applied_independently_per_entity_type():
    from app.api.graph import _limit_graph_nodes

    nodes = [
        {"id": f"concept-{index}", "type": "Concept"}
        for index in range(201)
    ] + [
        {"id": f"identifier-{index}", "type": "identifier"}
        for index in range(200)
    ]

    result = _limit_graph_nodes(nodes, limit=200, per_type=True)

    assert len(result["nodes"]) == 400
    assert result["limitMode"] == "perType"
    assert result["limitPerType"] == 200
    assert result["truncated"] is True
    assert result["truncatedByType"] == {"concept": True}
    assert result["typeStats"] == [
        {"type": "concept", "count": 200, "truncated": True},
        {"type": "identifier", "count": 200, "truncated": False},
    ]


def test_single_kb_graph_retains_total_entity_limit():
    from app.api.graph import _limit_graph_nodes

    nodes = [
        {"id": f"concept-{index}", "type": "concept"}
        for index in range(150)
    ] + [
        {"id": f"identifier-{index}", "type": "identifier"}
        for index in range(150)
    ]

    result = _limit_graph_nodes(nodes, limit=200, per_type=False)

    assert len(result["nodes"]) == 200
    assert result["limitMode"] == "total"
    assert result["truncated"] is True
