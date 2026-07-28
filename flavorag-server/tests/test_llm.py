"""Unit tests for LLM client module."""
import pytest
from app.llm.client import (
    LLMClient,
    MockLLMClient,
    collect_agentic_generation,
    get_llm_client,
)


class TestMockLLMClient:
    @pytest.mark.asyncio
    async def test_streams_tokens(self):
        client = MockLLMClient()
        tokens = []
        async for t in client.chat_stream([
            {"role": "user", "content": "hello"}
        ]):
            tokens.append(t)
        full = "".join(tokens)
        assert "Mock" in full
        assert len(full) > 0

    @pytest.mark.asyncio
    async def test_uses_question(self):
        client = MockLLMClient()
        tokens = []
        async for t in client.chat_stream([
            {"role": "user", "content": "what is python"}
        ]):
            tokens.append(t)
        full = "".join(tokens)
        assert "python" in full.lower() or "what" in full.lower()


class TestLLMFactory:
    def test_no_api_key_returns_mock(self):
        client = get_llm_client(api_key="")
        assert isinstance(client, MockLLMClient)

    def test_with_api_key_returns_real(self):
        client = get_llm_client(api_key="sk-fake-xxx")
        assert isinstance(client, LLMClient)


@pytest.mark.asyncio
async def test_agentic_generation_retries_three_primary_then_falls_back(monkeypatch):
    import app.llm.client as client_module

    calls: list[tuple[str, str, str]] = []

    async def fake_stream(self, messages, temperature=0.7, max_tokens=None):
        calls.append((self.model, self.base_url, self.api_key))
        if len(calls) <= 3:
            yield "discarded partial"
            raise RuntimeError(f"{self.model} unavailable")
        yield "fallback answer"

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(LLMClient, "chat_stream", fake_stream)
    monkeypatch.setattr(client_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(client_module._log, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(client_module.settings, "agentic_primary_model", "deepseek-v4-pro")
    monkeypatch.setattr(client_module.settings, "agentic_fallback_model", "deepseek-v4-flash")
    monkeypatch.setattr(client_module.settings, "agentic_model_api_key", "")
    monkeypatch.setattr(client_module.settings, "agentic_model_base_url", "")
    monkeypatch.setattr(client_module.settings, "agentic_primary_attempts", 3)
    monkeypatch.setattr(client_module.settings, "agentic_fallback_attempts", 2)
    monkeypatch.setattr(client_module.settings, "reasoning_api_key", "reasoning-key")
    monkeypatch.setattr(
        client_module.settings,
        "reasoning_base_url",
        "https://reasoning.example/v1",
    )
    monkeypatch.setattr(client_module.settings, "hyde_api_key", "hyde-key")
    monkeypatch.setattr(
        client_module.settings,
        "hyde_base_url",
        "https://hyde.example/v1",
    )
    monkeypatch.setattr(client_module.settings, "mem0_api_key", "mem0-key")
    monkeypatch.setattr(
        client_module.settings,
        "mem0_base_url",
        "https://mem0.example/v1",
    )

    result = await collect_agentic_generation(
        [{"role": "user", "content": "test"}],
        api_key="sk-test",
        base_url="https://example.invalid/v1",
    )

    assert calls == [
        ("deepseek-v4-pro", "https://reasoning.example/v1", "reasoning-key"),
        ("deepseek-v4-pro", "https://reasoning.example/v1", "reasoning-key"),
        ("deepseek-v4-pro", "https://reasoning.example/v1", "reasoning-key"),
        ("deepseek-v4-flash", "https://hyde.example/v1", "hyde-key"),
    ]
    assert result.tokens == ["fallback answer"]
    assert result.attempts == 4
    assert result.fallback_used is True
    assert len(result.failures) == 3


@pytest.mark.asyncio
async def test_agentic_generation_stops_after_five_attempts(monkeypatch):
    import app.llm.client as client_module

    calls: list[str] = []

    async def always_fail(self, messages, temperature=0.7, max_tokens=None):
        calls.append(self.model)
        raise RuntimeError("provider down")
        yield  # pragma: no cover - keeps this an async generator

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(LLMClient, "chat_stream", always_fail)
    monkeypatch.setattr(client_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(client_module._log, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(client_module.settings, "agentic_model_api_key", "")
    monkeypatch.setattr(client_module.settings, "agentic_model_base_url", "")
    monkeypatch.setattr(client_module.settings, "reasoning_api_key", "")
    monkeypatch.setattr(client_module.settings, "hyde_api_key", "")
    monkeypatch.setattr(client_module.settings, "mem0_api_key", "")
    monkeypatch.setattr(client_module.settings, "agentic_primary_attempts", 99)
    monkeypatch.setattr(client_module.settings, "agentic_fallback_attempts", 99)

    with pytest.raises(RuntimeError, match="provider down"):
        await collect_agentic_generation(
            [{"role": "user", "content": "test"}],
            api_key="sk-test",
            base_url="https://example.invalid/v1",
        )

    assert calls == [
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "deepseek-v4-flash",
    ]
