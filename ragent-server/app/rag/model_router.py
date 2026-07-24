"""Intent-based model router — selects model/endpoint per query intent."""
from __future__ import annotations

from app.config.settings import settings


class ModelRouter:
    """Route to the optimal LLM model based on query intent.

    Default mapping:
        code_search  -> deepseek-v3 (code generation oriented)
        document_qa  -> qwen-plus-latest (balanced QA)
        general      -> settings.llm_model
    """

    _INTENT_MODEL_ATTR: dict[str, str] = {
        "code_search": "code_model",
        "document_qa": "doc_model",
    }

    _DEFAULT_MODELS: dict[str, str] = {
        "code_search": "deepseek-v3",
        "document_qa": "qwen-plus-latest",
    }

    def __init__(
        self,
        code_model: str | None = None,
        doc_model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self._code_model = code_model or settings.code_model or "deepseek-v3"
        self._doc_model = doc_model or settings.doc_model or "qwen-plus-latest"
        self._base_url = (base_url or settings.llm_base_url).rstrip("/")
        self._api_key = api_key or settings.bailian_api_key or settings.siliconflow_api_key or ""

    def route(self, intent: str) -> tuple[str, str, str]:
        """Return (model_name, base_url, api_key) for the given intent."""
        model = self._resolve_model(intent)
        return (model, self._base_url, self._api_key)

    def _resolve_model(self, intent: str) -> str:
        if intent in self._INTENT_MODEL_ATTR:
            attr = self._INTENT_MODEL_ATTR[intent]
            model = getattr(settings, attr, None)
            if model:
                return model
            return self._DEFAULT_MODELS.get(intent, settings.llm_model)
        return settings.llm_model or "qwen-plus-latest"
