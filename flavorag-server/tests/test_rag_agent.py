from app.agent.rag_agent import _retrieval_tool_timeout_sec
from app.config.settings import settings


def test_retrieval_tool_timeout_covers_all_bounded_pipeline_phases(monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_timeout_sec", 10)
    monkeypatch.setattr(settings, "query_understanding_timeout_sec", 20.0)
    monkeypatch.setattr(settings, "retrieval_total_timeout_ms", 35000)
    monkeypatch.setattr(settings, "reranker_timeout_sec", 15.0)
    monkeypatch.setattr(settings, "hyde_enabled", True)
    monkeypatch.setattr(settings, "hyde_timeout_sec", 25.0)

    timeout = _retrieval_tool_timeout_sec(enable_hyde=True)

    assert timeout == 80.0
    assert timeout > settings.retrieval_total_timeout_ms / 1000
