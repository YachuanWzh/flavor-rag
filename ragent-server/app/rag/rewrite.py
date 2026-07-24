"""Query rewriting — lightweight prompt-based rewriter (no external LLM call by default)."""
from __future__ import annotations


async def rewrite_query(question: str, history: list[dict] | None = None) -> str | None:
    """Rewrite user query for better retrieval.

    In standalone mode: returns the original question unchanged.
    When LLM is configured: can call LLM to expand/normalise the query.

    Args:
        question: Raw user question.
        history: Recent conversation history for context.

    Returns:
        Rewritten query string, or None if no rewrite needed.
    """
    if not question:
        return None

    # Basic normalisation — trim and de-duplicate whitespace
    cleaned = " ".join(question.split())

    # For now: return cleaned version. Full LLM rewrite can be plugged in later.
    return cleaned if cleaned != question else None


async def rewrite_query_with_llm(
    question: str,
    history: list[dict] | None = None,
) -> str | None:
    """Full LLM-based query rewrite. Requires configured LLM client."""
    from app.llm.client import get_llm_client

    client = get_llm_client()
    # Skip rewrite for mock clients
    from app.llm.client import MockLLMClient
    if isinstance(client, MockLLMClient):
        return None

    history_context = ""
    if history:
        recent = history[-6:]
        history_context = "\n".join(
            f"{h['role']}: {h['content'][:200]}" for h in recent
        )

    prompt = [
        {
            "role": "system",
            "content": (
                "你是一个查询重写助手。将用户的模糊问题改写为更精确、更适合检索的查询语句。"
                "保留原意，补充可能的同义词和相关术语。只返回改写后的问题，不要加任何解释。"
            ),
        },
        {"role": "user", "content": f"对话历史:\n{history_context}\n\n问题: {question}\n\n改写:"},
    ]

    rewritten_parts: list[str] = []
    async for token in client.chat_stream(prompt, temperature=0.3):
        rewritten_parts.append(token)

    result = "".join(rewritten_parts).strip()
    return result if result and result != question else None
