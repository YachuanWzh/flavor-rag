"""LLM streaming client — OpenAI-compatible API + mock fallback."""
from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx
from app.config.settings import settings
from app.rag.governance import CircuitBreaker


_model_breakers: dict[tuple[str, str], CircuitBreaker] = {}


def _model_breaker(base_url: str, model: str) -> CircuitBreaker:
    key = (base_url, model)
    if key not in _model_breakers:
        _model_breakers[key] = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failures,
            recovery_timeout_sec=settings.circuit_breaker_recovery_sec,
        )
    return _model_breakers[key]


class LLMClient:
    """OpenAI-compatible streaming LLM client via HTTPX."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key if api_key is not None else (settings.bailian_api_key or settings.siliconflow_api_key)
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream chat completions, yielding tokens one at a time.

        Yields:
            Regular tokens as plain strings.
            Deep-thinking tokens prefixed with ``__THINK__``.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        breaker = _model_breaker(self.base_url, self.model)
        breaker.before_call()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data["choices"][0]["delta"]
                                if "content" in delta and delta["content"]:
                                    yield delta["content"]
                                if (
                                    "reasoning_content" in delta
                                    and delta["reasoning_content"]
                                ):
                                    yield f"__THINK__{delta['reasoning_content']}"
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
        except Exception:
            breaker.record_failure()
            raise
        else:
            breaker.record_success()


class MockLLMClient:
    """Local mock that simulates streaming responses without an API key."""

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Return a canned response token by token."""
        question = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                question = msg.get("content", "")
                break

        short_q = question[:30]
        canned = (
            f"这是一个模拟回复。关于 [{short_q}] 的问题，"
            "系统当前工作在 Mock 模式下（未配置 LLM API Key）。"
            "请在 .env 中配置 BAILIAN_API_KEY 或 SILICONFLOW_API_KEY 以启用真实对话。"
        )
        for char in canned:
            yield char
            time.sleep(0.015)  # simulate streaming delay


def get_llm_client(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMClient | MockLLMClient:
    """Factory: return real client when API key is set, else mock."""
    key = api_key if api_key is not None else (settings.bailian_api_key or settings.siliconflow_api_key)
    if not key:
        return MockLLMClient()
    return LLMClient(api_key=key, base_url=base_url, model=model)
