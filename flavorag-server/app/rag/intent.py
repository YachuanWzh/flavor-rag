"""Database-backed intent classification and routing."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import or_, select

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.session import async_session_factory
from app.models import IntentNode

_intent_log = get_logger("flavorag.rag.intent")

INTENT_TAXONOMY: dict[str, str] = {
    "code_search": "查询代码、函数、类、API接口、编程实现相关",
    "document_qa": "查询文档、说明、使用指南、操作手册、介绍相关",
    "knowledge_qa": "查询知识库、专业知识、百科、教程、学习资料相关",
    "data_query": "查询具体数据、统计数字、表格信息、结构化数据相关",
    "general": "通用问答、闲聊、无法归类的问题",
}
_SYSTEM_TERMS = {
    "你好", "您好", "hi", "hello", "嗨", "谢谢", "感谢",
    "你是谁", "你能做什么", "再见",
}


@dataclass(frozen=True)
class IntentMatch:
    intent_code: str
    name: str
    score: float
    kind: str = "KB"
    kb_id: str | None = None
    collection_name: str | None = None
    search_channels: list[str] = field(default_factory=list)
    prompt_template: str | None = None
    mcp_tool_id: str | None = None
    full_path: str = ""
    reason: str = ""
    score_threshold: float = 0.3


@dataclass(frozen=True)
class SubqueryIntent:
    query: str
    matches: list[IntentMatch] = field(default_factory=list)


@dataclass(frozen=True)
class IntentResolution:
    subqueries: list[SubqueryIntent]
    system_only: bool = False
    needs_guidance: bool = False
    guidance_prompt: str = ""

    @property
    def primary(self) -> IntentMatch | None:
        matches = [match for item in self.subqueries for match in item.matches]
        return max(matches, key=lambda item: item.score, default=None)

    @property
    def search_channels(self) -> list[str]:
        ordered: list[str] = []
        for item in self.subqueries:
            for match in item.matches:
                for channel in match.search_channels:
                    normalized = channel.lower().replace("-", "_")
                    if normalized not in ordered:
                        ordered.append(normalized)
        return ordered

    def to_dict(self) -> dict:
        primary = self.primary
        return {
            "intent": primary.intent_code if primary else "general",
            "name": primary.name if primary else "通用问答",
            "confidence": primary.score if primary else 0.0,
            "collection_name": primary.collection_name if primary else None,
            "kb_id": primary.kb_id if primary else None,
            "kind": primary.kind if primary else "KB",
            "system_only": self.system_only,
            "needs_guidance": self.needs_guidance,
            "guidance_prompt": self.guidance_prompt,
            "search_channels": self.search_channels,
            "subqueries": [
                {
                    "query": item.query,
                    "matches": [match.__dict__ for match in item.matches],
                }
                for item in self.subqueries
            ],
        }


def _is_system_query(question: str) -> bool:
    compact = re.sub(r"[\s，。！？!?、,.]", "", question).lower()
    return compact in _SYSTEM_TERMS or (
        len(compact) <= 12 and any(term in compact for term in _SYSTEM_TERMS)
    )


async def _load_intent_nodes(
    *,
    kb_id: str | None,
    tenant_id: str,
) -> list[IntentNode]:
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(IntentNode).where(
                    IntentNode.deleted == 0,
                    IntentNode.enabled == 1,
                    IntentNode.tenant_id == tenant_id,
                    or_(
                        IntentNode.kb_id.is_(None),
                        IntentNode.kb_id == "",
                        *([IntentNode.kb_id == kb_id] if kb_id else []),
                    ),
                )
            )
            return list(result.scalars().all())
    except Exception as exc:
        _intent_log.warning("intent_tree_load_failed", error=type(exc).__name__)
        return []


def _full_paths(nodes: list[IntentNode]) -> dict[str, str]:
    by_code = {node.intent_code: node for node in nodes}
    paths: dict[str, str] = {}

    def build(node: IntentNode, seen: set[str]) -> str:
        if node.intent_code in paths:
            return paths[node.intent_code]
        if node.intent_code in seen:
            return node.name
        parent = by_code.get(node.parent_intent_code or "")
        value = f"{build(parent, seen | {node.intent_code})} > {node.name}" if parent else node.name
        paths[node.intent_code] = value
        return value

    for node in nodes:
        build(node, set())
    return paths


def _leaf_nodes(nodes: list[IntentNode]) -> list[IntentNode]:
    parents = {node.parent_intent_code for node in nodes if node.parent_intent_code}
    return [node for node in nodes if node.intent_code not in parents]


def _lexical_score(question: str, node: IntentNode, full_path: str) -> float:
    haystack = " ".join(
        [
            node.name or "",
            node.description or "",
            full_path,
            " ".join(node.examples or []),
        ]
    ).lower()
    query = question.lower().strip()
    if not query:
        return 0.0
    terms = {
        item
        for item in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)
        if item
    }
    hits = sum(1 for term in terms if term in haystack)
    direct = 0.35 if node.name and node.name.lower() in query else 0.0
    return min(0.95, direct + (hits / max(1, len(terms))) * 0.65)


def _node_to_match(
    node: IntentNode,
    score: float,
    *,
    path: str,
    reason: str = "",
) -> IntentMatch:
    return IntentMatch(
        intent_code=node.intent_code,
        name=node.name,
        score=round(max(0.0, min(1.0, score)), 4),
        kind=(node.kind or "KB").upper(),
        kb_id=node.kb_id,
        collection_name=node.collection_name,
        search_channels=list(node.search_channels or []),
        prompt_template=node.prompt_template,
        mcp_tool_id=node.mcp_tool_id,
        full_path=path,
        reason=reason,
        score_threshold=(node.score_threshold if node.score_threshold is not None else 30) / 100,
    )


async def _llm_classify_nodes(
    question: str,
    nodes: list[IntentNode],
    paths: dict[str, str],
) -> list[IntentMatch]:
    from app.llm.client import MockLLMClient, get_llm_client

    client = get_llm_client()
    if isinstance(client, MockLLMClient):
        return []
    candidates = [
        {
            "id": node.intent_code,
            "path": paths[node.intent_code],
            "description": node.description or "",
            "kind": node.kind or "KB",
            "examples": node.examples or [],
        }
        for node in nodes
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "从候选意图中选择与问题相关的意图并评分。不要编造候选外的 id。"
                "只返回 JSON 数组，每项格式为 "
                '{"id":"...","score":0.0,"reason":"..."}。'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"question": question, "candidates": candidates},
                ensure_ascii=False,
            ),
        },
    ]
    tokens: list[str] = []
    async for token in client.chat_stream(messages, temperature=0.1):
        if not token.startswith("__THINK__"):
            tokens.append(token)
    raw = "".join(tokens)
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    payload = json.loads(match.group(0))
    by_code = {node.intent_code: node for node in nodes}
    matches: list[IntentMatch] = []
    for item in payload:
        node = by_code.get(str(item.get("id", "")))
        if not node:
            continue
        matches.append(
            _node_to_match(
                node,
                float(item.get("score", 0)),
                path=paths[node.intent_code],
                reason=str(item.get("reason", ""))[:160],
            )
        )
    return matches


async def _classify_subquery(
    question: str,
    nodes: list[IntentNode],
    paths: dict[str, str],
) -> SubqueryIntent:
    if _is_system_query(question):
        return SubqueryIntent(
            question,
            [IntentMatch("system_chat", "系统对话", 1.0, kind="SYSTEM")],
        )
    matches: list[IntentMatch] = []
    if nodes and settings.intent_llm_enabled and (
        settings.bailian_api_key or settings.siliconflow_api_key
    ):
        try:
            matches = await _llm_classify_nodes(question, nodes, paths)
        except Exception as exc:
            _intent_log.warning("intent_llm_failed", error=type(exc).__name__)
    if not matches:
        matches = [
            _node_to_match(
                node,
                _lexical_score(question, node, paths[node.intent_code]),
                path=paths[node.intent_code],
                reason="lexical_fallback",
            )
            for node in nodes
        ]
    matches = [
        match
        for match in matches
        if match.score >= max(settings.intent_min_score, match.score_threshold)
    ]
    matches.sort(key=lambda item: item.score, reverse=True)
    return SubqueryIntent(question, matches[: settings.intent_max_matches])


async def resolve_intents(
    subqueries: list[str],
    *,
    kb_id: str | None,
    tenant_id: str,
) -> IntentResolution:
    queries = [query for query in subqueries if query.strip()]
    tree_nodes = await _load_intent_nodes(kb_id=kb_id, tenant_id=tenant_id)
    paths = _full_paths(tree_nodes)
    nodes = _leaf_nodes(tree_nodes)
    classified = await asyncio.gather(
        *(_classify_subquery(query, nodes, paths) for query in queries)
    )
    system_only = bool(classified) and all(
        item.matches and all(match.kind == "SYSTEM" for match in item.matches)
        for item in classified
    )
    needs_guidance = False
    guidance = ""
    if len(classified) == 1 and len(classified[0].matches) >= 2:
        top, second = classified[0].matches[:2]
        distinct_targets = (top.kb_id, top.kind) != (second.kb_id, second.kind)
        if (
            distinct_targets
            and top.score >= settings.intent_guidance_min_score
            and top.score - second.score <= settings.intent_guidance_score_gap
        ):
            needs_guidance = True
            guidance = (
                "这个问题可能对应两个不同的知识范围。你想了解的是：\n"
                f"1. {top.full_path or top.name}\n"
                f"2. {second.full_path or second.name}\n"
                "请补充选择，我会按对应资料继续查找。"
            )
    return IntentResolution(
        list(classified),
        system_only=system_only,
        needs_guidance=needs_guidance,
        guidance_prompt=guidance,
    )


async def recognize_intent(question: str) -> dict:
    """Backward-compatible intent entry point."""
    if not question:
        return {"intent": "general", "collection_name": None, "confidence": 0.0}
    resolution = await resolve_intents(
        [question],
        kb_id=None,
        tenant_id="default",
    )
    if resolution.primary:
        return resolution.to_dict()
    return _rule_classify(question)


def _rule_classify(question: str) -> dict:
    if _is_system_query(question):
        return {
            "intent": "general",
            "collection_name": None,
            "confidence": 1.0,
            "kind": "SYSTEM",
            "system_only": True,
        }
    lowered = question.lower()
    code_hits = sum(
        term in lowered
        for term in ("代码", "函数", "class ", "def ", "api", "报错", "编程")
    )
    doc_hits = sum(
        term in lowered
        for term in ("文档", "说明", "怎么用", "指南", "流程", "配置")
    )
    intent = "code_search" if code_hits > doc_hits else "document_qa" if doc_hits else "general"
    return {
        "intent": intent,
        "collection_name": None,
        "confidence": min(0.9, 0.5 + max(code_hits, doc_hits) * 0.1),
        "kind": "KB",
        "system_only": False,
    }
