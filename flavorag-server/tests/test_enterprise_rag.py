import asyncio

import pytest

from app.rag.search.base import SearchResult


def test_acl_is_tenant_scoped_and_document_acl_can_only_narrow():
    from app.security.access import (
        AccessGrant,
        Permission,
        Principal,
        Resource,
        is_allowed,
    )

    principal = Principal(
        user_id="u1",
        tenant_id="tenant-a",
        department_id="engineering",
        role="user",
    )
    kb = Resource(
        resource_type="KNOWLEDGE_BASE",
        resource_id="kb1",
        tenant_id="tenant-a",
        owner_id="owner",
        department_id="engineering",
    )
    document = Resource(
        resource_type="DOCUMENT",
        resource_id="doc1",
        tenant_id="tenant-a",
        owner_id="owner",
        department_id="finance",
        parent_id="kb1",
    )
    grants = [
        AccessGrant("USER", "u1", "DOCUMENT", "doc1", Permission.READ),
    ]

    assert is_allowed(principal, kb, Permission.READ, []) is True
    assert is_allowed(
        principal,
        document,
        Permission.READ,
        grants,
        parent_allowed=True,
    ) is True
    assert is_allowed(
        Principal("u1", "tenant-b", "engineering", "user"),
        kb,
        Permission.READ,
        [],
    ) is False
    assert is_allowed(
        principal,
        document,
        Permission.READ,
        grants,
        parent_allowed=False,
    ) is False


@pytest.mark.asyncio
async def test_private_url_and_redirect_targets_are_rejected():
    from app.ingestion.url_fetcher import SafeURLFetcher, URLSecurityError

    async def resolver(hostname: str) -> list[str]:
        return {
            "example.com": ["93.184.216.34"],
            "internal.example": ["127.0.0.1"],
        }[hostname]

    fetcher = SafeURLFetcher(resolver=resolver)
    await fetcher.validate_url("https://example.com/report.pdf")

    with pytest.raises(URLSecurityError):
        await fetcher.validate_url("http://internal.example/secret")
    with pytest.raises(URLSecurityError):
        await fetcher.validate_url("file:///etc/passwd")
    with pytest.raises(URLSecurityError):
        await fetcher.validate_url("https://user:password@example.com/report")


@pytest.mark.asyncio
async def test_circuit_breaker_opens_and_recovers():
    from app.rag.governance import CircuitBreaker, CircuitOpenError

    now = [100.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_sec=10,
        clock=lambda: now[0],
    )

    async def fail():
        raise RuntimeError("down")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(fail)
    with pytest.raises(CircuitOpenError):
        await breaker.call(fail)

    now[0] += 11

    async def succeed():
        return "ok"

    assert await breaker.call(succeed) == "ok"
    assert breaker.state == "closed"


def test_threshold_and_context_budget_are_enforced():
    from app.rag.governance import RetrievalBudget, select_context

    candidates = [
        SearchResult("a", "A" * 60, 0.91),
        SearchResult("b", "B" * 60, 0.79),
        SearchResult("c", "C" * 20, 0.20),
    ]
    selected, decision = select_context(
        candidates,
        RetrievalBudget(context_max_chars=100, final_top_k=5),
        min_score=0.5,
    )

    assert [item.chunk_id for item in selected] == ["a"]
    assert decision.answerable is True
    assert decision.dropped_below_threshold == 1
    assert decision.dropped_by_budget == 1

    selected, decision = select_context(
        [SearchResult("x", "irrelevant", 0.1)],
        RetrievalBudget(),
        min_score=0.5,
    )
    assert selected == []
    assert decision.answerable is False
    assert decision.reason == "insufficient_relevance"


def test_context_selection_restores_score_order_after_reranking_and_quota():
    from app.rag.governance import RetrievalBudget, select_context

    candidates = [
        SearchResult(
            "low",
            "low",
            0.2,
            doc_id="doc-1",
            metadata={"kb_id": "kb-a"},
        ),
        SearchResult(
            "high",
            "high",
            0.9,
            doc_id="doc-1",
            metadata={"kb_id": "kb-a"},
        ),
        SearchResult(
            "middle",
            "middle",
            0.7,
            doc_id="doc-2",
            metadata={"kb_id": "kb-a"},
        ),
    ]
    budget = RetrievalBudget(context_max_tokens=100, final_top_k=3)

    selected, _ = select_context(candidates, budget, min_score=0)

    assert [item.chunk_id for item in selected] == ["high", "middle", "low"]

    selected, _ = select_context(
        candidates,
        RetrievalBudget(context_max_tokens=100, final_top_k=2),
        min_score=0,
        kb_quota=1,
        fallback_pool=[
            *candidates,
            SearchResult(
                "kb-b",
                "fallback",
                0.1,
                doc_id="doc-b",
                metadata={"kb_id": "kb-b"},
            ),
        ],
    )

    assert [item.chunk_id for item in selected] == ["high", "kb-b"]


@pytest.mark.asyncio
async def test_parallel_channels_are_bounded_and_isolated():
    from app.rag.governance import RetrievalBudget, run_search_channels

    active = 0
    peak = 0

    async def channel(name: str):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        if name == "broken":
            raise RuntimeError("optional channel failed")
        return [SearchResult(name, name, 1.0)]

    results, statuses = await run_search_channels(
        {
            "vector": lambda: channel("vector"),
            "keyword": lambda: channel("keyword"),
            "broken": lambda: channel("broken"),
        },
        RetrievalBudget(channel_timeout_ms=500, max_candidates=2),
    )

    assert peak >= 2
    assert sum(len(items) for items in results.values()) == 2
    assert statuses["broken"].status == "error"


@pytest.mark.asyncio
async def test_parallel_channels_enforce_total_timeout():
    from app.rag.governance import RetrievalBudget, run_search_channels

    async def slow_channel():
        await asyncio.sleep(0.2)
        return [SearchResult("late", "late", 1.0)]

    results, statuses = await run_search_channels(
        {"vector": slow_channel},
        RetrievalBudget(channel_timeout_ms=500, total_timeout_ms=20),
    )

    assert results["vector"] == []
    assert statuses["vector"].status == "timeout"
    assert statuses["vector"].error == "total_timeout"


def test_trace_finalize_accepts_rejection_metadata():
    import inspect

    from app.rag.trace import TraceLogger

    parameters = inspect.signature(TraceLogger.finalize).parameters
    assert "rejection_reason" in parameters
    assert "metadata" in parameters


def test_reprocess_endpoint_accepts_chunk_configuration():
    import inspect

    from app.api.knowledge import reprocess_document

    parameters = inspect.signature(reprocess_document).parameters
    assert {"chunk_strategy", "chunk_size", "overlap"}.issubset(parameters)


@pytest.mark.asyncio
async def test_reranker_disabled_is_passthrough(monkeypatch):
    from app.rag.postprocess import reranker as reranker_module

    monkeypatch.setattr(reranker_module.settings, "reranker_enabled", False)
    candidates = [SearchResult("chunk-1", "content", 0.8)]
    result = await reranker_module.Reranker(api_key="unused").rerank(
        "query",
        candidates,
    )
    assert result == candidates


def test_sql_tool_policy_rejects_mutation_and_unscoped_queries():
    from app.tools.sql_tool import SQLPolicyError, validate_readonly_sql

    normalized = validate_readonly_sql(
        "SELECT id, name FROM sales_view WHERE tenant_id = :tenant_id LIMIT 20",
        allowed_relations={"sales_view"},
    )
    assert normalized.startswith("SELECT")

    for unsafe in (
        "DELETE FROM sales_view WHERE tenant_id = :tenant_id",
        "SELECT * FROM sales_view; SELECT * FROM users",
        "SELECT * FROM sales_view",
        "SELECT * FROM private_table WHERE tenant_id = :tenant_id",
    ):
        with pytest.raises(SQLPolicyError):
            validate_readonly_sql(unsafe, allowed_relations={"sales_view"})


@pytest.mark.asyncio
async def test_controlled_agent_enforces_allowlist_and_step_budget():
    from app.agent.controlled import AgentAction, ControlledAgent
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()

    async def retrieve(arguments, context):
        return {"evidence": arguments["query"], "tenant": context["tenant_id"]}

    registry.register("retrieve", retrieve, read_only=True)
    actions = iter(
        [
            AgentAction("retrieve", {"query": "q1"}),
            AgentAction("retrieve", {"query": "q2"}),
            AgentAction("retrieve", {"query": "q3"}),
        ]
    )
    agent = ControlledAgent(registry, planner=lambda state: next(actions), max_steps=2)
    result = await agent.run("question", {"tenant_id": "t1"})

    assert result.status == "budget_exhausted"
    assert len(result.steps) == 2

    registry.register("write_sql", retrieve, read_only=False)
    with pytest.raises(PermissionError):
        await registry.invoke("write_sql", {}, {"tenant_id": "t1"})


def test_evaluation_investment_decisions_require_labeled_uplift():
    from app.evaluation.decision import decide_optional_retrieval

    hold = decide_optional_retrieval(
        capability="graph",
        labeled_case_count=0,
        baseline_recall=0.5,
        candidate_recall=0.9,
        latency_p95_ms=100,
        min_cases=5,
        min_recall_uplift=0.05,
        max_latency_p95_ms=500,
    )
    assert hold.decision == "HOLD"

    enable = decide_optional_retrieval(
        capability="graph",
        labeled_case_count=10,
        baseline_recall=0.5,
        candidate_recall=0.62,
        latency_p95_ms=200,
        min_cases=5,
        min_recall_uplift=0.05,
        max_latency_p95_ms=500,
    )
    assert enable.decision == "ENABLE"


@pytest.mark.asyncio
async def test_scanned_pdf_page_uses_ocr_and_keeps_coordinates(tmp_path):
    from reportlab.pdfgen import canvas

    from app.ingestion.pdf.ocr import OCRText
    from app.ingestion.pdf.parser import StructuredPdfParser

    pdf_path = tmp_path / "scan.pdf"
    document = canvas.Canvas(str(pdf_path), pagesize=(200, 300))
    document.showPage()
    document.save()

    class StubOCR:
        enabled = True

        async def recognize(self, image_bytes, *, page_no, page_width, page_height):
            assert image_bytes.startswith(b"\x89PNG")
            return [
                OCRText(
                    text="scanned invoice total 100",
                    x0=0.1,
                    top=0.2,
                    x1=0.8,
                    bottom=0.3,
                    confidence=0.97,
                    normalized=True,
                )
            ]

    parsed = await StructuredPdfParser(
        ocr_provider=StubOCR(),
        ocr_min_native_chars=10,
    ).parse_file(str(pdf_path))

    ocr_blocks = [
        block for block in parsed.blocks
        if block.metadata.get("extraction_method") == "ocr"
    ]
    assert len(ocr_blocks) == 1
    assert ocr_blocks[0].content == "scanned invoice total 100"
    assert ocr_blocks[0].first_bbox.page_no == 1
    assert ocr_blocks[0].first_bbox.x0 == pytest.approx(20)
    assert ocr_blocks[0].metadata["confidence"] == pytest.approx(0.97)


@pytest.mark.asyncio
async def test_pipeline_propagates_existing_trace_id(monkeypatch):
    import app.rag.pipeline as pipeline_module
    from app.rag.pipeline import RAGContext, RAGPipeline

    async def unchanged(question, history):
        return question

    async def intent(question):
        return {"intent": "general"}

    async def authorized(session, principal, results, kb_id):
        return results

    monkeypatch.setattr(pipeline_module, "rewrite_query", unchanged)
    monkeypatch.setattr(pipeline_module, "recognize_intent", intent)
    monkeypatch.setattr(pipeline_module, "filter_authorized_results", authorized)
    monkeypatch.setattr(pipeline_module.settings, "es_enabled", False)
    monkeypatch.setattr(pipeline_module.settings, "graph_enabled", False)

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(pipeline_module, "async_session_factory", SessionContext)

    class Trace:
        def __init__(self):
            self.ids = []

        async def trace_node(self, trace_run_id, *args, **kwargs):
            self.ids.append(trace_run_id)

    class Vector:
        async def search(self, query, collection_name, top_k):
            return [SearchResult("chunk-1", "evidence", 0.9, doc_id="doc-1")]

    class Rerank:
        async def rerank(self, query, candidates, top_n):
            return candidates

    trace = Trace()
    pipeline = RAGPipeline(trace_logger=trace)
    pipeline.milvus = Vector()
    pipeline.reranker = Rerank()

    async def keep(results):
        return results

    async def metadata(results, **kwargs):
        return None

    pipeline._filter_unavailable_chunks = keep
    pipeline._resolve_metadata = metadata
    result = await pipeline.run(
        RAGContext(
            question="question",
            kb_id="kb-1",
            collection_name="collection",
            trace_run_id="trace-123",
            user_id="u1",
            tenant_id="t1",
        )
    )

    assert result.trace_run_id == "trace-123"
    assert trace.ids
    assert set(trace.ids) == {"trace-123"}


@pytest.mark.asyncio
async def test_all_channel_post_filter_blocks_cross_tenant_chunk(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database.sqlite_schema import initialize_sqlite_schema
    from app.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
    from app.security.access import Principal
    from app.security.service import filter_authorized_results

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'acl.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await initialize_sqlite_schema(engine)
        async with sessions() as session:
            session.add(
                KnowledgeBase(
                    id="kb-a",
                    name="private",
                    embedding_model="model",
                    collection_name="private_collection",
                    tenant_id="tenant-a",
                    visibility="PRIVATE",
                    created_by="owner-a",
                )
            )
            session.add(
                KnowledgeDocument(
                    id="doc-a",
                    kb_id="kb-a",
                    tenant_id="tenant-a",
                    visibility="INHERIT",
                    doc_name="secret.txt",
                    file_url="secret.txt",
                    file_type="txt",
                    created_by="owner-a",
                )
            )
            session.add(
                KnowledgeChunk(
                    id="chunk-a",
                    kb_id="kb-a",
                    doc_id="doc-a",
                    tenant_id="tenant-a",
                    chunk_index=0,
                    content="secret",
                    enabled=1,
                    created_by="owner-a",
                )
            )
            await session.commit()

        async with sessions() as session:
            filtered = await filter_authorized_results(
                session,
                Principal("user-b", "tenant-b", "", "user"),
                [SearchResult("chunk-a", "secret", 1.0, doc_id="doc-a")],
                kb_id="kb-a",
            )
        assert filtered == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_sync_cleans_all_external_channels(monkeypatch):
    import app.services.index_sync as sync_module
    from app.models import KnowledgeAsset, KnowledgeBase, KnowledgeChunk
    from app.services.index_sync import IndexSyncService

    calls = []

    class Session:
        def add(self, value):
            self.value = value

        async def flush(self):
            return None

    class Milvus:
        def delete_by_ids(self, collection, ids):
            calls.append(("milvus", collection, ids))

    class ES:
        async def delete_by_ids(self, ids):
            calls.append(("es", ids))

    class Graph:
        async def delete_document(self, kb_id, doc_id):
            calls.append(("graph", kb_id, doc_id))

    class Storage:
        async def delete_keys(self, keys):
            calls.append(("storage", keys))

    monkeypatch.setattr(sync_module, "MilvusSearchChannel", Milvus)
    monkeypatch.setattr(sync_module, "ESKeywordSearchChannel", ES)
    monkeypatch.setattr(sync_module, "Neo4jGraphStore", Graph)
    monkeypatch.setattr(sync_module, "LightRAGClient", Graph)
    monkeypatch.setattr(sync_module, "S3PdfAssetStorage", Storage)

    kb = KnowledgeBase(
        id="kb1",
        name="kb",
        embedding_model="model",
        collection_name="collection",
        tenant_id="t1",
        created_by="u1",
    )
    chunk = KnowledgeChunk(
        id="c1",
        kb_id="kb1",
        doc_id="d1",
        tenant_id="t1",
        chunk_index=0,
        content="content",
        created_by="u1",
    )
    asset = KnowledgeAsset(
        id="a1",
        kb_id="kb1",
        doc_id="d1",
        tenant_id="t1",
        mime_type="image/png",
        content_hash="hash",
        storage_key="assets/key",
        storage_url="https://storage/key",
        created_by="u1",
    )
    job = await IndexSyncService().delete_document(
        Session(),
        kb=kb,
        doc_id="d1",
        chunks=[chunk],
        assets=[asset],
    )

    assert job.status == "SUCCESS"
    assert {call[0] for call in calls} == {"milvus", "es", "graph", "storage"}
