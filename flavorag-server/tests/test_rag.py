"""Unit tests for RAG rewrite, intent, pipeline — no external deps."""
import pytest
from app.rag.rewrite import rewrite_query
from app.rag.intent import recognize_intent
from app.rag.pipeline import RAGPipeline, RAGContext, RAGResult


class TestRewrite:
    @pytest.mark.asyncio
    async def test_normal_text_unchanged(self):
        result = await rewrite_query("hello world")
        # Clean text — same after cleaning, returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_whitespace_cleaned(self):
        result = await rewrite_query("  hello   world  ")
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_empty_question(self):
        result = await rewrite_query("")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_history(self):
        result = await rewrite_query(
            "what about python?",
            [{"role": "user", "content": "I like rust"}, {"role": "assistant", "content": "rust is great"}],
        )
        assert result is None  # already clean


class TestIntent:
    @pytest.mark.asyncio
    async def test_code_search_intent(self):
        result = await recognize_intent("这个函数是做什么的")
        assert result["intent"] == "code_search"

    @pytest.mark.asyncio
    async def test_document_intent(self):
        result = await recognize_intent("如何使用这个项目")
        assert result["intent"] == "document_qa"

    @pytest.mark.asyncio
    async def test_general_intent(self):
        result = await recognize_intent("今天天气怎么样")
        assert result["intent"] == "general"


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
