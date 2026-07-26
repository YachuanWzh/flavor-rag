from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class OCRText:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    confidence: float = 0.0
    normalized: bool = True


class OCRProvider(Protocol):
    enabled: bool

    async def recognize(
        self,
        image_bytes: bytes,
        *,
        page_no: int,
        page_width: float,
        page_height: float,
    ) -> list[OCRText]: ...


class DisabledOCRProvider:
    enabled = False

    async def recognize(self, image_bytes: bytes, **kwargs) -> list[OCRText]:
        return []


class OpenAICompatibleOCRProvider:
    """Page OCR through an OpenAI-compatible vision model.

    The model is required to return normalized coordinates, which are converted
    to PDF page coordinates by the parser.
    """

    enabled = True

    def __init__(self, *, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def recognize(
        self,
        image_bytes: bytes,
        *,
        page_no: int,
        page_width: float,
        page_height: float,
    ) -> list[OCRText]:
        encoded = base64.b64encode(image_bytes).decode()
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "OCR this page. Return JSON {blocks:[{text,x0,top,x1,bottom,"
                        "confidence}]}. Coordinates must be normalized to 0..1. "
                        "Preserve reading order and do not infer invisible text."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"PDF page {page_no}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                },
            ],
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        data = json.loads(raw)
        output: list[OCRText] = []
        for block in data.get("blocks", []):
            text = str(block.get("text", "")).strip()
            if not text:
                continue
            output.append(
                OCRText(
                    text=text,
                    x0=float(block.get("x0", 0)),
                    top=float(block.get("top", 0)),
                    x1=float(block.get("x1", 1)),
                    bottom=float(block.get("bottom", 1)),
                    confidence=float(block.get("confidence", 0)),
                    normalized=True,
                )
            )
        return output


def get_ocr_provider() -> OCRProvider:
    from app.config.settings import settings

    api_key = settings.vlm_api_key or settings.bailian_api_key
    base_url = settings.vlm_base_url or settings.llm_base_url
    if not settings.pdf_ocr_enabled or not api_key or not base_url:
        return DisabledOCRProvider()
    return OpenAICompatibleOCRProvider(
        base_url=base_url,
        api_key=api_key,
        model=settings.vlm_model,
    )
