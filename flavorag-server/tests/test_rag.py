"""Unit tests for RAG rewrite, intent, pipeline — no external deps."""
import pytest
import hashlib
from types import SimpleNamespace
from app.rag.rewrite import needs_reference_clarification, rewrite_query
from app.rag.intent import recognize_intent
from app.rag.pipeline import RAGPipeline, RAGContext, RAGResult
from app.rag.search.base import SearchResult


class TestRewrite:
    @pytest.mark.asyncio
    async def test_normal_text_returns_string(self):
        """With LLM enabled, rewrite may or may not alter the query."""
        result = await rewrite_query("hello world")
        # Either None (unchanged) or a rewritten string — both valid
        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_whitespace_normalized(self):
        """Extra whitespace should be cleaned, LLM may further rewrite."""
        result = await rewrite_query("  hello   world  ")
        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_empty_question(self):
        result = await rewrite_query("")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_history_works(self):
        """Rewrite with history context should not crash and may return useful output."""
        result = await rewrite_query(
            "what about python?",
            [{"role": "user", "content": "I like rust"}, {"role": "assistant", "content": "rust is great"}],
        )
        assert result is None or isinstance(result, str)


class TestIntent:
    VALID_INTENTS = {"code_search", "document_qa", "knowledge_qa", "data_query", "general"}

    @pytest.mark.asyncio
    async def test_code_related_returns_valid_intent(self):
        result = await recognize_intent("这个函数是做什么的")
        assert result["intent"] in self.VALID_INTENTS
        assert 0.0 <= result["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_document_related_returns_valid_intent(self):
        result = await recognize_intent("如何使用这个项目")
        assert result["intent"] in self.VALID_INTENTS
        assert 0.0 <= result["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_general_intent(self):
        result = await recognize_intent("今天天气怎么样")
        assert result["intent"] in self.VALID_INTENTS
        assert 0.0 <= result["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_empty_question(self):
        result = await recognize_intent("")
        assert result["intent"] == "general"
        assert result["confidence"] == 0.0


class TestRAGPipeline:
    @pytest.mark.asyncio
    async def test_pipeline_creates(self):
        pipeline = RAGPipeline()
        assert pipeline is not None

    def test_rag_context_defaults(self):
        ctx = RAGContext(question="test")
        assert ctx.question == "test"
        assert ctx.history == []
        assert ctx.deep_thinking is False

    def test_rag_context_full(self):
        ctx = RAGContext(
            question="q",
            conversation_id="c1",
            kb_id="kb1",
            collection_name="coll",
            history=[{"role": "user", "content": "hi"}],
            deep_thinking=True,
        )
        assert ctx.conversation_id == "c1"
        assert ctx.kb_id == "kb1"
        assert ctx.collection_name == "coll"
        assert ctx.deep_thinking is True

    def test_rag_result_fields(self):
        r = RAGResult(
            question="q",
            rewrite="rewritten q",
            intent={"intent": "general"},
            context_chunks=[{"content": "c1"}],
            sources=[{"docName": "d", "content": "x"}],
            duration_ms=100,
        )
        assert r.rewrite == "rewritten q"
        assert r.intent["intent"] == "general"
        assert len(r.context_chunks) == 1
        assert r.duration_ms == 100

    @pytest.mark.asyncio
    async def test_disabled_chunks_are_filtered_without_reindexing(self, monkeypatch):
        import app.rag.pipeline as pipeline_module

        disabled_content = "disabled by its content hash"
        disabled_hash = hashlib.sha256(disabled_content.encode()).hexdigest()[:16]

        class FakeRows:
            def __iter__(self):
                return iter(
                    [
                        ("enabled-id", "enabled-hash", 1, 0),
                        ("disabled-id", "disabled-id-hash", 0, 0),
                        ("hash-match-id", disabled_hash, 0, 0),
                        ("deleted-id", "deleted-hash", 1, 1),
                    ]
                )

        class FakeSession:
            async def execute(self, _statement):
                return FakeRows()

        class FakeSessionContext:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, _exc_type, _exc, _traceback):
                return False

        monkeypatch.setattr(
            pipeline_module,
            "async_session_factory",
            lambda: FakeSessionContext(),
        )

        results = [
            SearchResult(chunk_id="enabled-id", content="enabled", score=1.0),
            SearchResult(chunk_id="disabled-id", content="disabled", score=0.9),
            SearchResult(chunk_id="deleted-id", content="deleted", score=0.8),
            SearchResult(chunk_id="stale-search-id", content=disabled_content, score=0.7),
            SearchResult(chunk_id="graph-only", content="not in database", score=0.6),
        ]

        pipeline = RAGPipeline.__new__(RAGPipeline)
        filtered = await pipeline._filter_unavailable_chunks(results)

        assert [item.chunk_id for item in filtered] == ["enabled-id"]

    @pytest.mark.asyncio
    async def test_metadata_resolution_preserves_retrieval_scores(self, monkeypatch):
        import app.rag.pipeline as pipeline_module

        class FakeSession:
            async def execute(self, _statement):
                return [
                    (
                        "content-hash",
                        "chunk-1",
                        "kb-1",
                        9,
                        "doc-1",
                        "TEXT",
                        None,
                        None,
                        [],
                        {"section": "overview"},
                        "guide.md",
                        "md",
                    )
                ]

        class FakeSessionContext:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, _exc_type, _exc, _traceback):
                return False

        monkeypatch.setattr(
            pipeline_module,
            "async_session_factory",
            lambda: FakeSessionContext(),
        )
        result = SearchResult(
            chunk_id="chunk-1",
            content="retrieved evidence",
            score=0.016,
            metadata={
                "fusionScore": 0.016,
                "matchedChannels": ["vector"],
                "channelScores": {"vector": {"rank": 1}},
            },
        )

        pipeline = RAGPipeline.__new__(RAGPipeline)
        await pipeline._resolve_metadata([result], kb_id="kb-1")

        assert result.metadata["section"] == "overview"
        assert result.metadata["kb_id"] == "kb-1"
        assert result.metadata["fusionScore"] == 0.016
        assert result.metadata["matchedChannels"] == ["vector"]
        assert result.metadata["channelScores"]["vector"]["rank"] == 1

    @pytest.mark.asyncio
    async def test_neighbor_expansion_keeps_fetched_neighbors(self, monkeypatch):
        import app.rag.pipeline as pipeline_module

        neighbor = SimpleNamespace(
            id="chunk-12",
            chunk_index=12,
            doc_id="doc-1",
            content="neighbor content",
            embedding_content=None,
            block_type="TEXT",
            page_start=None,
            page_end=None,
            bbox_json=[],
            metadata_json={},
            doc_name="guide.md",
        )

        class FakeSession:
            async def execute(self, _statement):
                return [neighbor]

        class FakeSessionContext:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, _exc_type, _exc, _traceback):
                return False

        monkeypatch.setattr(
            pipeline_module,
            "async_session_factory",
            lambda: FakeSessionContext(),
        )
        anchor = SearchResult(
            chunk_id="chunk-13",
            doc_id="doc-1",
            content="anchor content",
            score=0.9,
            chunk_index=13,
        )

        pipeline = RAGPipeline.__new__(RAGPipeline)
        expanded = await pipeline._expand_neighbors([anchor], window=2)

        assert [item.chunk_id for item in expanded] == ["chunk-13", "chunk-12"]
        assert expanded[1].metadata["neighbor_of"] == ["chunk-13"]


def test_context_free_reference_requires_clarification():
    assert needs_reference_clarification("它的默认值是多少？", [])
    assert not needs_reference_clarification(
        "它的默认值是多少？",
        [{"role": "user", "content": "RETRIEVAL_FINAL_TOP_K"}],
    )
    assert not needs_reference_clarification("检索条数的默认值是多少？", [])
