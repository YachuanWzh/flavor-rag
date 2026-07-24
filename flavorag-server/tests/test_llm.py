"""Unit tests for LLM client module."""
import pytest
from app.llm.client import MockLLMClient, get_llm_client, LLMClient


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
