"""Intent recognition — classify user query intent for routing.

Supports:
- LLM-based classification (when intent_llm_enabled=True and API key available)
- Rule-based quick classification (fallback)
"""

from __future__ import annotations

from app.config.settings import settings
from app.config.logging_config import get_logger

_intent_log = get_logger("flavorag.rag.intent")

# Intent taxonomy with descriptions for LLM classification
INTENT_TAXONOMY: dict[str, str] = {
    "code_search": "查询代码、函数、类、API接口、编程实现相关",
    "document_qa": "查询文档、说明、使用指南、操作手册、介绍相关",
    "knowledge_qa": "查询知识库、专业知识、百科、教程、学习资料相关",
    "data_query": "查询具体数据、统计数字、表格信息、结构化数据相关",
    "general": "通用问答、闲聊、无法归类的问题",
}


async def recognize_intent(question: str) -> dict:
    """Recognize user intent from the query.

    Returns a dict with intent metadata:
        {
            "intent": "code_search" | "document_qa" | "knowledge_qa" | "data_query" | "general",
            "collection_name": None,
            "confidence": 0.0-1.0,
        }
    """
    if not question:
        return {"intent": "general", "collection_name": None, "confidence": 0.0}

    # Try LLM-based classification when enabled
    if settings.intent_llm_enabled:
        key = settings.bailian_api_key or settings.siliconflow_api_key
        if key:
            try:
                result = await _llm_classify(question)
                if result:
                    _intent_log.info("llm_intent", question=question[:60], intent=result["intent"])
                    return result
            except Exception as exc:
                _intent_log.warning("llm_intent_failed_fallback", error=str(exc))

    # Fallback: rule-based classification
    return _rule_classify(question)


def _rule_classify(question: str) -> dict:
    """Rule-based quick classification as fallback."""
    code_keywords = [
        "代码", "函数", "类", "方法", "import", "def ", "class ", "接口", "API",
        "参数", "返回值", "调用", "模块", "包", "编译", "运行时", "异常",
        "如何写", "怎么实现", "编程", "debug", "报错",
    ]
    doc_keywords = [
        "文档", "说明", "README", "怎么用", "如何使用", "介绍", "指南",
        "教程", "手册", "步骤", "流程", "规范", "配置",
    ]

    question_lower = question.lower()

    # Count keyword matches for confidence scoring
    code_hits = sum(1 for kw in code_keywords if kw.lower() in question_lower)
    doc_hits = sum(1 for kw in doc_keywords if kw.lower() in question_lower)

    if code_hits > doc_hits:
        return {"intent": "code_search", "collection_name": None, "confidence": min(0.5 + code_hits * 0.1, 0.9)}
    if doc_hits > code_hits:
        return {"intent": "document_qa", "collection_name": None, "confidence": min(0.5 + doc_hits * 0.1, 0.9)}
    if code_hits > 0 or doc_hits > 0:
        # Tie — check question length for context
        return {"intent": "document_qa" if len(question) > 30 else "code_search", "collection_name": None, "confidence": 0.5}

    return {"intent": "general", "collection_name": None, "confidence": 0.5}


async def _llm_classify(question: str) -> dict | None:
    """LLM-based intent classification.

    Prompts the LLM to classify the query into one of the predefined intents,
    returning structured JSON.
    """
    import json
    from app.llm.client import get_llm_client, MockLLMClient

    client = get_llm_client()
    if isinstance(client, MockLLMClient):
        return None

    taxonomy_text = "\n".join(f"- {k}: {v}" for k, v in INTENT_TAXONOMY.items())

    prompt = [
        {
            "role": "system",
            "content": (
                "你是一个查询意图分类器。根据用户问题判断其意图类型。\n\n"
                f"意图类型定义：\n{taxonomy_text}\n\n"
                "请返回 JSON 格式：\n"
                '{"intent": "<类型>", "collection_name": null, "confidence": 0.0-1.0}\n'
                "只返回 JSON，不要加任何解释。"
            ),
        },
        {"role": "user", "content": f"问题: {question}"},
    ]

    parts: list[str] = []
    async for token in client.chat_stream(prompt, temperature=0.1):
        parts.append(token)

    text = "".join(parts).strip()

    # Extract JSON from response (may be wrapped in markdown code blocks)
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract just the JSON object
        import re
        match = re.search(r'\{[^}]+\}', text)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            return None

    if not isinstance(result, dict):
        return None

    intent = result.get("intent", "general")
    if intent not in INTENT_TAXONOMY:
        intent = "general"

    confidence = float(result.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    return {"intent": intent, "collection_name": result.get("collection_name"), "confidence": confidence}
