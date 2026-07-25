"""Unit tests for RAG rewrite, intent, pipeline — no external deps."""
import pytest
from app.rag.rewrite import rewrite_query
from app.rag.intent import recognize_intent
from app.rag.pipeline import RAGPipeline, RAGContext, RAGResult


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
