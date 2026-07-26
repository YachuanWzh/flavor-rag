"""Write-time VLM enrichment for PDF images."""

from __future__ import annotations

import base64
from typing import Protocol

import httpx


class ImageDescriber(Protocol):
    async def describe(
        self, image_bytes: bytes, mime_type: str, *, context: str = ""
    ) -> str:
        ...


class NoopImageDescriber:
    async def describe(
        self, image_bytes: bytes, mime_type: str, *, context: str = ""
    ) -> str:
        return ""


class OpenAICompatibleImageDescriber:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_output_tokens: int = 800,
        timeout_seconds: float = 90.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds

    async def describe(
        self, image_bytes: bytes, mime_type: str, *, context: str = ""
    ) -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        prompt = (
            "请把这张企业文档图片转换成可检索的中文知识文本。"
            "先说明图片或图表的主题，再完整抄录可见文字；"
            "如果是图表，说明图例、坐标轴、趋势、关键数值和单位。"
            "不要猜测不可见内容，不要输出 Markdown 图片链接。"
        )
        if context:
            prompt += f"\n文档上下文：{context[:500]}"
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                    },
                ],
            }],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return str(content or "").strip()


def get_image_describer() -> ImageDescriber:
    from app.config.settings import settings

    if not settings.vlm_enabled:
        return NoopImageDescriber()
    api_key = settings.vlm_api_key or settings.bailian_api_key or settings.siliconflow_api_key
    if not api_key:
        return NoopImageDescriber()
    return OpenAICompatibleImageDescriber(
        base_url=settings.vlm_base_url or settings.llm_base_url,
        api_key=api_key,
        model=settings.vlm_model,
        max_output_tokens=settings.vlm_max_output_tokens,
    )
