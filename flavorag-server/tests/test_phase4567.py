"""Comprehensive tests for Phases 4-7: ES, Reranker, Trace, RateLimit, Router, Graph."""
import pytest
from dataclasses import dataclass
from app.rag.search.base import SearchResult


# ========== Rate Limiter ==========

class TestRateLimiter:
    def test_import_and_create(self):
        from app.rag.rate_limiter import RateLimiter
        rl = RateLimiter()
        assert rl is not None
        assert rl.user_limit == 60
        assert rl.ip_limit == 600

    def test_custom_limits(self):
        from app.rag.rate_limiter import RateLimiter
        rl = RateLimiter(user_limit=30, ip_limit=300)
        assert rl.user_limit == 30
        assert rl.ip_limit == 300

    @pytest.mark.asyncio
    async def test_graceful_degradation(self):
        from app.rag.rate_limiter import RateLimiter
        rl = RateLimiter()
        # With no Redis running, should always pass through
        result = await rl.check_user("test_user_123")
        assert result is True
        result = await rl.check_ip("127.0.0.1")
        assert result is True


# ========== Model Router ==========

class TestModelRouter:
    def test_import_and_create(self):
        from app.rag.model_router import ModelRouter
        mr = ModelRouter()
        assert mr is not None

    def test_route_code_search(self):
        from app.rag.model_router import ModelRouter
        mr = ModelRouter(code_model="deepseek-v3", doc_model="qwen-plus")
        model, url, key = mr.route("code_search")
        assert "deepseek" in model

    def test_route_document_qa(self):
        from app.rag.model_router import ModelRouter
        mr = ModelRouter(code_model="deepseek-v3", doc_model="qwen-plus")
        model, url, key = mr.route("document_qa")
        assert "qwen" in model

    def test_route_general_returns_default(self):
        from app.rag.model_router import ModelRouter
        mr = ModelRouter()
        model, url, key = mr.route("general")
        assert isinstance(model, str)
        assert len(model) > 0


# ========== ES Keyword Channel ==========

class TestESKeywordChannel:
    def test_import(self, monkeypatch):
        from app.config.settings import settings
        from app.rag.search.keyword import ESKeywordSearchChannel
        monkeypatch.setattr(settings, "es_enabled", False)
        chan = ESKeywordSearchChannel()
        assert chan is not None
        assert chan.enabled is False

    @pytest.mark.asyncio
    async def test_search_disabled_returns_empty(self, monkeypatch):
        from app.config.settings import settings
        from app.rag.search.keyword import ESKeywordSearchChannel
        monkeypatch.setattr(settings, "es_enabled", False)
        chan = ESKeywordSearchChannel()
        results = await chan.search("test query", "test_collection")
        assert results == []


# ========== Reranker ==========

class TestReranker:
    def test_import(self):
        from app.rag.postprocess.reranker import Reranker
        r = Reranker()
        assert r is not None

    @pytest.mark.asyncio
    async def test_no_api_key_returns_original(self):
        from app.rag.postprocess.reranker import Reranker
        r = Reranker(api_key="")  # No API key
        candidates = [
            SearchResult(chunk_id="a", content="first", score=0.9),
            SearchResult(chunk_id="b", content="second", score=0.5),
            SearchResult(chunk_id="c", content="third", score=0.3),
        ]
        result = await r.rerank("query", candidates, top_n=2)
        assert len(result) == 2
        assert result[0].chunk_id == "a"

    @pytest.mark.asyncio
    async def test_empty_candidates(self):
        from app.rag.postprocess.reranker import Reranker
        r = Reranker(api_key="sk-fake")
        result = await r.rerank("query", [], top_n=5)
        assert result == []

    def test_passthrough_strategy(self):
        from app.rag.postprocess.reranker import Reranker
        r = Reranker(strategy="PASSTHROUGH")
        assert r.strategy == "PASSTHROUGH"


# ========== Trace Logger ==========

class TestTraceLogger:
    def test_import(self):
        from app.rag.trace import TraceLogger
        assert TraceLogger is not None

    def test_none_trace_handled(self):
        # Trace logger needs DB session, but class creation shouldn't fail
        # Just verify module structure
        from app.rag.trace import TraceLogger
        methods = ['trace_query', 'trace_node', 'finalize', 'get_trace']
        for m in methods:
            assert hasattr(TraceLogger, m)


# ========== Graph Client ==========

class TestGraphClient:
    def test_import(self):
        from app.rag.graph.lightrag_client import LightRAGClient
        client = LightRAGClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_graph_disabled_returns_empty(self, monkeypatch):
        from app.rag.graph.lightrag_client import LightRAGClient
        from app.config.settings import settings
        monkeypatch.setattr(settings, "graph_enabled", False)
        client = LightRAGClient()
        result = await client.query_graph("test query")
        assert "results" in result


# ========== Integration: Correct file structure ==========

class TestIntegration:
    def test_all_modules_exist(self):
        modules = [
            "app.rag.search.keyword",
            "app.rag.postprocess.reranker",
            "app.rag.graph.lightrag_client",
            "app.rag.trace",
            "app.rag.rate_limiter",
            "app.rag.model_router",
            "app.api.admin",
        ]
        for mod in modules:
            __import__(mod)

    def test_admin_endpoints(self):
        from app.api.admin import router
        paths = [r.path for r in router.routes]
        assert "/api/admin/health" in paths
        assert "/api/admin/traces" in paths
        assert "/api/admin/traces/{trace_id}" in paths

    def test_pipeline_accepts_trace_logger(self):
        from app.rag.pipeline import RAGPipeline
        p = RAGPipeline(trace_logger=None)
        assert p._trace is None
