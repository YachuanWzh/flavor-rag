from app.agent.rag_agent import _retrieval_tool_timeout_sec
from app.config.settings import settings

import pytest


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


@pytest.mark.asyncio
async def test_agent_retries_preserve_named_scope_narrowing(monkeypatch):
    import app.agent.rag_agent as agent_module
    from app.agent.controlled import AgentAction
    from app.rag.pipeline import RAGContext, RAGResult, RetrievalScope

    calls = []

    class Pipeline:
        async def run(self, context):
            calls.append(
                (context.question, [scope.kb_id for scope in context.retrieval_scopes])
            )
            answerable = len(calls) > 1
            return RAGResult(
                question=context.question,
                rewrite=None,
                intent={"intent": "general"},
                context_chunks=(
                    [{"content": "evidence", "score": 0.9}] if answerable else []
                ),
                sources=([{"chunkId": "chunk-1"}] if answerable else []),
                duration_ms=1,
                answerable=answerable,
                rejection_reason=None if answerable else "retrieval_unavailable",
            )

    async def retry_with_generic_query(**_kwargs):
        return AgentAction("retrieve", {"query": "compare the systems"})

    monkeypatch.setattr(agent_module, "plan_next_action", retry_with_generic_query)
    monkeypatch.setattr(settings, "agent_max_steps", 3)
    monkeypatch.setattr(settings, "sql_tool_enabled", False)
    monkeypatch.setattr(settings, "mcp_tool_enabled", False)
    scopes = [
        RetrievalScope("kb-code", "flavor-code", "collection-code"),
        RetrievalScope("kb-rag", "flavor-rag", "collection-rag"),
        RetrievalScope("kb-agent", "huamulan-agent", "collection-agent"),
    ]

    await agent_module.ControlledRAGAgent(Pipeline()).run(
        RAGContext(
            question="flavor-code 和 flavor-rag 有哪些结合点？",
            retrieval_scopes=scopes,
        )
    )

    assert calls == [
        (
            "flavor-code 和 flavor-rag 有哪些结合点？",
            ["kb-code", "kb-rag"],
        ),
        ("compare the systems", ["kb-code", "kb-rag"]),
    ]
