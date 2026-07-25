"""Query rewriting — lightweight prompt-based rewriter with optional LLM enhancement."""

from __future__ import annotations

from app.config.settings import settings
from app.config.logging_config import get_logger

_rewrite_log = get_logger("flavorag.rag.rewrite")


async def rewrite_query(question: str, history: list[dict] | None = None) -> str | None:
    """Rewrite user query for better retrieval.

    When ``settings.rewrite_enabled`` is True and an LLM API key is configured,
    uses LLM-based rewriting (context-aware). Otherwise falls back to basic
    text normalisation.

    Args:
        question: Raw user question.
        history: Recent conversation history for context.

    Returns:
        Rewritten query string, or None if no rewrite needed.
    """
    if not question:
        return None

    # Try LLM rewrite when enabled and API key is available
    if settings.rewrite_enabled:
        key = settings.bailian_api_key or settings.siliconflow_api_key
        if key:
            try:
                result = await rewrite_query_with_llm(question, history)
                if result:
                    _rewrite_log.info("llm_rewrite", original=question[:60], rewritten=result[:60])
                    return result
            except Exception as exc:
                _rewrite_log.warning("llm_rewrite_failed_fallback", error=str(exc))

    # Fallback: basic normalisation
    cleaned = " ".join(question.split())
    return cleaned if cleaned != question else None


async def rewrite_query_with_llm(
    question: str,
    history: list[dict] | None = None,
) -> str | None:
    """Full LLM-based query rewrite. Requires configured LLM client.

    Expands user queries by:
    - Resolving pronouns & implicit references from history
    - Adding synonyms & domain terminology
    - Converting colloquial expressions to formal search queries
    """
    from app.llm.client import get_llm_client, MockLLMClient

    client = get_llm_client()
    if isinstance(client, MockLLMClient):
        return None

    history_context = ""
    if history:
        recent = history[-6:]
        history_context = "\n".join(
            f"{h['role']}: {h['content'][:200]}" for h in recent if h.get("content")
        )

    prompt = [
        {
            "role": "system",
            "content": (
                "你是一个查询重写助手。将用户的模糊问题改写为更精确、更适合检索的查询语句。"
                "遵循以下规则：\n"
                "1. 将口语化表达转为书面语（如'怎么弄'→'如何操作'）\n"
                "2. 补充可能的同义词和相关术语（如'报销'可补充'费用申请、财务审批'）\n"
                "3. 根据对话历史补全省略的上下文（如'那个呢'→补全具体指代）\n"
                "4. 保留原意，不要添加无关信息\n"
                "只返回改写后的问题，不要加任何解释。"
            ),
        },
        {"role": "user", "content": f"对话历史:\n{history_context}\n\n问题: {question}\n\n改写:"},
    ]

    rewritten_parts: list[str] = []
    async for token in client.chat_stream(prompt, temperature=0.3):
        rewritten_parts.append(token)

    result = "".join(rewritten_parts).strip()
    return result if result and result != question else None
