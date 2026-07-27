"""HyDE — Hypothetical Document Embeddings generation.

Uses a lightweight LLM to draft a hypothetical answer document for the user's
query, then embeds that document for vector retrieval.  The generated document
bridges the query-document semantic gap, improving recall for short / ambiguous
queries without replacing existing retrieval channels.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.config.logging_config import get_logger
from app.config.settings import settings

_log = get_logger("flavorag.rag.hyde")

HYDE_SYSTEM_PROMPT = (
    "你是一个知识检索助手。给定用户的问题，请写出一段可能包含答案的文档片段。"
    "要求：\n"
    "1. 以陈述句撰写，模拟真实文档的语气和结构\n"
    "2. 包含具体的事实、数据或步骤（可以合理推测）\n"
    "3. 长度控制在 200-400 字\n"
    "4. 不要使用‘根据问题’等元描述，直接输出文档内容\n"
    "5. 如果问题涉及代码，输出包含代码片段的文档"
)


@dataclass
class HyDEResult:
    """Result of a single HyDE generation attempt."""

    hypothetical_doc: str        # 生成的假设文档
    model_used: str              # 实际使用的模型
    duration_ms: int             # 生成耗时（毫秒）
    timed_out: bool = False      # 是否超时降级


async def generate_hypothetical_document(
    query: str,
    *,
    history: list[dict] | None = None,
) -> HyDEResult:
    """Use a lightweight model to generate a hypothetical answer document.

    On timeout or failure returns an empty document (graceful degradation —
    the caller simply skips the HyDE retrieval channel).
    """
    t0 = time.time()
    model = settings.hyde_model or "qwen-turbo-latest"
    base_url = (settings.hyde_base_url or settings.llm_base_url).rstrip("/")
    api_key = (
        settings.hyde_api_key
        or settings.bailian_api_key
        or settings.siliconflow_api_key
    )

    # Late import to avoid circular dependencies in test contexts
    from app.llm.client import MockLLMClient, get_llm_client

    client = get_llm_client(api_key=api_key, base_url=base_url, model=model)
    if isinstance(client, MockLLMClient):
        return HyDEResult(hypothetical_doc="", model_used="mock", duration_ms=0)

    # Build prompt — include a small slice of conversation history for context
    context_lines = ""
    if history:
        recent = history[-4:]
        context_lines = "\n".join(
            f"{m['role']}: {str(m.get('content', ''))[:200]}" for m in recent
        )

    messages: list[dict] = [
        {"role": "system", "content": HYDE_SYSTEM_PROMPT},
    ]
    if context_lines:
        messages.append({"role": "user", "content": f"对话上下文：\n{context_lines}"})
        messages.append({"role": "assistant", "content": "好的，我已了解上下文。"})
    messages.append({"role": "user", "content": f"问题：{query}"})

    try:
        tokens: list[str] = []
        async with asyncio.timeout(settings.hyde_timeout_sec):
            async for token in client.chat_stream(
                messages, temperature=settings.hyde_temperature
            ):
                if not token.startswith("__THINK__"):
                    tokens.append(token)
                    # Hard truncate: prevent lightweight model from over-generating
                    if len("".join(tokens)) > settings.hyde_max_tokens * 2:
                        break

        doc = "".join(tokens).strip()
        duration_ms = int((time.time() - t0) * 1000)
        _log.info("hyde_generated", model=model, doc_len=len(doc), took_ms=duration_ms)
        return HyDEResult(
            hypothetical_doc=doc, model_used=model, duration_ms=duration_ms
        )
    except (asyncio.TimeoutError, TimeoutError):
        duration_ms = int((time.time() - t0) * 1000)
        _log.warning("hyde_timeout", model=model, took_ms=duration_ms)
        return HyDEResult(
            hypothetical_doc="", model_used=model,
            duration_ms=duration_ms, timed_out=True,
        )
    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        _log.warning(
            "hyde_failed", model=model, error=str(exc)[:200], took_ms=duration_ms
        )
        return HyDEResult(
            hypothetical_doc="", model_used=model, duration_ms=duration_ms
        )
