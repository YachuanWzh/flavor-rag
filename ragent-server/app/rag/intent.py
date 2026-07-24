"""Intent recognition — classify user query intent for routing."""
from __future__ import annotations


async def recognize_intent(question: str) -> dict:
    """Recognize user intent from the query.

    Returns a dict with intent metadata:
        {
            "intent": "code_search" | "document_qa" | "general",
            "collection_name": "rag_xxx" | None,
            "confidence": 0.0-1.0,
        }

    In standalone mode: returns default intent.
    Can be extended with LLM-based classification or rule-based patterns.
    """
    # Rule-based quick classification
    code_keywords = ["代码", "函数", "类", "import", "def ", "class ", "接口", "API"]
    doc_keywords = ["文档", "说明", "README", "怎么用", "如何使用", "介绍", "使用"]

    question_lower = question.lower()

    if any(kw in question_lower for kw in code_keywords):
        return {
            "intent": "code_search",
            "collection_name": None,
            "confidence": 0.7,
        }

    if any(kw in question_lower for kw in doc_keywords):
        return {
            "intent": "document_qa",
            "collection_name": None,
            "confidence": 0.7,
        }

    return {
        "intent": "general",
        "collection_name": None,
        "confidence": 0.5,
    }
