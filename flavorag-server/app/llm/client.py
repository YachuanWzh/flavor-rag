"""LLM streaming client — OpenAI-compatible API + mock fallback."""
from __future__ import annotations

import json
import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from app.config.settings import settings
from app.observability.metrics import (
    LLM_FIRST_TOKEN,
    LLM_STREAM_DURATION,
    LLM_STREAM_FAILURES,
    LLM_TOKENS,
    LLM_MODEL_FALLBACKS,
    LLM_RETRY_ATTEMPTS,
)
from app.rag.governance import CircuitBreaker
from app.config.logging_config import get_logger


_model_breakers: dict[tuple[str, str], CircuitBreaker] = {}
_log = get_logger("flavorag.llm")

# ─── TTFT optimization: persistent HTTP client pool ───
# Avoids repeated TCP/TLS handshakes for every LLM call (rewrite, intent, generation).
_shared_client: httpx.AsyncClient | None = None


def _get_shared_client() -> httpx.AsyncClient:
    """Return a module-level persistent AsyncClient with connection pooling."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=60,
            ),
            http2=True,
        )
    return _shared_client


def _model_breaker(base_url: str, model: str) -> CircuitBreaker:
    key = (base_url, model)
    if key not in _model_breakers:
        _model_breakers[key] = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failures,
            recovery_timeout_sec=settings.circuit_breaker_recovery_sec,
            name=f"llm:{model}",
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
        max_tokens: int | None = None,
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
            "max_tokens": max_tokens or settings.llm_max_output_tokens,
            "stream_options": {"include_usage": True},
        }
        breaker = _model_breaker(self.base_url, self.model)
        breaker.before_call()
        stream_started = time.monotonic()
        first_token_at: float | None = None
        client = _get_shared_client()
        try:
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
                            usage = data.get("usage") or {}
                            if usage:
                                for token_type, field in (
                                    ("input", "prompt_tokens"),
                                    ("output", "completion_tokens"),
                                ):
                                    value = int(usage.get(field) or 0)
                                    if value:
                                        LLM_TOKENS.labels(
                                            model=self.model,
                                            type=token_type,
                                        ).inc(value)
                            delta = data["choices"][0]["delta"]
                            if "content" in delta and delta["content"]:
                                if first_token_at is None:
                                    first_token_at = time.monotonic()
                                    LLM_FIRST_TOKEN.labels(model=self.model).observe(
                                        first_token_at - stream_started
                                    )
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
            LLM_STREAM_FAILURES.labels(model=self.model).inc()
            raise
        else:
            breaker.record_success()
            LLM_STREAM_DURATION.labels(model=self.model).observe(
                time.monotonic() - stream_started
            )


class MockLLMClient:
    """Local mock that simulates streaming responses without an API key."""

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
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
            await asyncio.sleep(0.015)  # simulate streaming delay


@dataclass(frozen=True)
class AgenticGenerationResult:
    tokens: list[str]
    model: str
    attempts: int
    fallback_used: bool
    failures: list[dict]


async def collect_agentic_generation(
    messages: list[dict],
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
) -> AgenticGenerationResult:
    """Generate atomically with exponential backoff and a bounded model fallback.

    Tokens are buffered per attempt. A failed partial stream is discarded, so
    callers never leak duplicate/half answers to the user when the next model
    succeeds.
    """
    primary_attempts = max(1, min(3, settings.agentic_primary_attempts))
    fallback_attempts = max(0, min(2, settings.agentic_fallback_attempts))
    primary_model = (
        settings.agentic_primary_model
        or settings.reasoning_model
        or "deepseek-v4-pro"
    )
    fallback_model = (
        settings.agentic_fallback_model
        or settings.hyde_model
        or settings.mem0_model
        or "deepseek-v4-flash"
    )

    def resolve_provider(
        candidates: list[tuple[str | None, str | None]],
    ) -> tuple[str, str]:
        for candidate_key, candidate_url in candidates:
            if candidate_key:
                return (
                    candidate_key,
                    (
                        settings.agentic_model_base_url
                        or candidate_url
                        or settings.llm_base_url
                    ).rstrip("/"),
                )
        return "", (
            settings.agentic_model_base_url or settings.llm_base_url
        ).rstrip("/")

    # Agentic primary generation follows the existing DeepSeek reasoning
    # configuration. HyDE and Mem0 provide the lightweight DeepSeek fallback.
    # The routed model provider remains a final compatibility fallback.
    primary_key, primary_url = resolve_provider(
        [
            (
                settings.agentic_model_api_key,
                settings.agentic_model_base_url
                or settings.reasoning_base_url
                or base_url,
            ),
            (settings.reasoning_api_key, settings.reasoning_base_url),
            (settings.hyde_api_key, settings.hyde_base_url),
            (settings.mem0_api_key, settings.mem0_base_url),
            (api_key, base_url),
            (settings.bailian_api_key, settings.llm_base_url),
            (settings.siliconflow_api_key, settings.llm_base_url),
        ]
    )
    fallback_key, fallback_url = resolve_provider(
        [
            (
                settings.agentic_model_api_key,
                settings.agentic_model_base_url
                or settings.hyde_base_url
                or settings.mem0_base_url
                or primary_url,
            ),
            (settings.hyde_api_key, settings.hyde_base_url),
            (settings.mem0_api_key, settings.mem0_base_url),
            (settings.reasoning_api_key, settings.reasoning_base_url),
            (primary_key, primary_url),
        ]
    )
    plan = [
        *(
            ("primary", primary_model, primary_key, primary_url)
            for _ in range(primary_attempts)
        ),
        *(
            ("fallback", fallback_model, fallback_key, fallback_url)
            for _ in range(fallback_attempts)
        ),
    ][:5]

    # Local development keeps the existing deterministic mock behavior.
    if not primary_key and not fallback_key:
        client = MockLLMClient()
        tokens = [
            token
            async for token in client.chat_stream(
                messages, max_tokens=max_tokens or settings.llm_max_output_tokens
            )
        ]
        return AgenticGenerationResult(tokens, "mock", 1, False, [])

    failures: list[dict] = []
    fallback_observed = False
    last_error: Exception | None = None
    for index, (tier, model, resolved_key, resolved_url) in enumerate(plan):
        if tier == "fallback" and not fallback_observed:
            fallback_observed = True
            LLM_MODEL_FALLBACKS.inc()
        client = LLMClient(api_key=resolved_key, base_url=resolved_url, model=model)
        try:
            async with asyncio.timeout(settings.llm_generation_timeout_sec):
                tokens = [
                    token
                    async for token in client.chat_stream(
                        messages,
                        max_tokens=max_tokens or settings.llm_max_output_tokens,
                    )
                ]
            if not tokens:
                raise RuntimeError("LLM returned an empty stream")
            LLM_RETRY_ATTEMPTS.labels(
                model=model, tier=tier, outcome="success"
            ).inc()
            return AgenticGenerationResult(
                tokens=tokens,
                model=model,
                attempts=index + 1,
                fallback_used=tier == "fallback",
                failures=failures,
            )
        except Exception as exc:
            last_error = exc
            failures.append(
                {
                    "attempt": index + 1,
                    "model": model,
                    "tier": tier,
                    "errorType": type(exc).__name__,
                    "error": (str(exc) or type(exc).__name__)[:300],
                }
            )
            LLM_RETRY_ATTEMPTS.labels(
                model=model, tier=tier, outcome="failure"
            ).inc()
            _log.error(
                "agentic_generation_attempt_failed",
                attempt=index + 1,
                model=model,
                tier=tier,
                error_type=type(exc).__name__,
                error=(str(exc) or type(exc).__name__)[:300],
            )
            if index + 1 < len(plan):
                delay = min(
                    settings.agentic_retry_max_delay_sec,
                    settings.agentic_retry_base_delay_sec * (2 ** index),
                )
                await asyncio.sleep(max(0, delay))

    if last_error is not None:
        raise last_error
    raise RuntimeError("Agentic generation retry plan is empty")


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
