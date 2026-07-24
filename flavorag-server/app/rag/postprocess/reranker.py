"""Reranker — cross-encoder / LLM-based post-recall re-ranking."""
from __future__ import annotations

import httpx
from app.config.settings import settings
from app.rag.search.base import SearchResult


class Reranker:
    """Re-rank search results for better precision.

    Supports:
    - CROSS_ENCODER: call remote rerank API (e.g., SiliconFlow/Jina Reranker)
    - LLM_BASED: prompt LLM to re-order candidates
    - PASSTHROUGH: return unchanged when no API key
    """

    STRATEGY_CROSS_ENCODER = "CROSS_ENCODER"
    STRATEGY_LLM_BASED = "LLM_BASED"
    STRATEGY_PASSTHROUGH = "PASSTHROUGH"

    def __init__(
        self,
        strategy: str = STRATEGY_CROSS_ENCODER,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.strategy = strategy
        self.api_key = api_key or settings.siliconflow_api_key
        self.base_url = (base_url or settings.reranker_base_url).rstrip("/")
        self.model = model or settings.reranker_model

    async def rerank(
        self, query: str, candidates: list[SearchResult], top_n: int = 5
    ) -> list[SearchResult]:
        if not candidates:
            return []

        if not self.api_key:
            return candidates[:top_n]

        if self.strategy == self.STRATEGY_CROSS_ENCODER:
            return await self._cross_encoder_rerank(query, candidates, top_n)
        elif self.strategy == self.STRATEGY_LLM_BASED:
            return await self._llm_based_rerank(query, candidates, top_n)
        else:
            return candidates[:top_n]

    async def _cross_encoder_rerank(
        self, query: str, candidates: list[SearchResult], top_n: int
    ) -> list[SearchResult]:
        try:
            documents = [c.content for c in candidates]
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.base_url}/rerank",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                # Map back to original SearchResult order
                reranked: list[SearchResult] = []
                for r in results:
                    idx = r["index"]
                    if idx < len(candidates):
                        reranked.append(candidates[idx])
                return reranked[:top_n]
        except Exception:
            return candidates[:top_n]

    async def _llm_based_rerank(
        self, query: str, candidates: list[SearchResult], top_n: int
    ) -> list[SearchResult]:
        """Prompt LLM to re-order candidates."""
        from app.llm.client import get_llm_client, MockLLMClient

        client = get_llm_client()
        if isinstance(client, MockLLMClient):
            return candidates[:top_n]

        doc_text = "\n".join(
            f"[{i}] {c.content[:300]}" for i, c in enumerate(candidates)
        )
        prompt = [
            {
                "role": "system",
                "content": "你是一个文档重排序助手。根据用户查询选出最相关的文档，只返回文档编号列表（如：[2, 0, 4]），不要加任何解释。",
            },
            {
                "role": "user",
                "content": f"查询: {query}\n\n候选文档:\n{doc_text}\n\n选出最相关的 {top_n} 个文档编号列表:",
            },
        ]

        try:
            parts: list[str] = []
            async for token in client.chat_stream(prompt, temperature=0.1):
                parts.append(token)
            full = "".join(parts)

            # Parse indices from response
            import re
            nums = re.findall(r"\d+", full)
            reranked: list[SearchResult] = []
            seen: set[int] = set()
            for n in nums:
                idx = int(n)
                if 0 <= idx < len(candidates) and idx not in seen:
                    reranked.append(candidates[idx])
                    seen.add(idx)
                    if len(reranked) >= top_n:
                        break
            return reranked if reranked else candidates[:top_n]
        except Exception:
            return candidates[:top_n]
