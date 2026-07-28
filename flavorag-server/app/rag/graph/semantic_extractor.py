"""Evidence-grounded LLM extraction for the native knowledge graph."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.llm.client import LLMClient
from app.rag.graph.neo4j_store import Neo4jGraphStore

_log = get_logger("flavorag.graph.semantic")

ENTITY_TYPES = frozenset(
    {
        "system",
        "service",
        "component",
        "api",
        "data",
        "model",
        "algorithm",
        "standard",
        "organization",
        "person",
        "concept",
        "technology",
    }
)

RELATION_TYPES = frozenset(
    {
        "USES",
        "DEPENDS_ON",
        "IMPLEMENTS",
        "PART_OF",
        "EXPOSES",
        "PRODUCES",
        "CONSUMES",
        "STORES_IN",
        "EVALUATED_ON",
        "COMPARED_WITH",
        "EXTENDS",
        "INTEGRATES_WITH",
    }
)

_SYSTEM_PROMPT_TEMPLATE = """你是知识图谱关系抽取器。只依据给定原文输出 JSON，不要解释。
规则：
1. 实体必须在原文中逐字出现，禁止补充常识或猜测。
2. 关系必须由同一 chunk 中的一段原文直接支持。
3. JSON、API、HTTP 等通用词可以作为句内实体，但不能因为“都出现过”就推断关系。
   {endpoint_rule}
4. 关系类型只能取：USES, DEPENDS_ON, IMPLEMENTS, PART_OF, EXPOSES,
   PRODUCES, CONSUMES, STORES_IN, EVALUATED_ON, COMPARED_WITH, EXTENDS,
   INTEGRATES_WITH。
5. confidence 是 0 到 1。没有明确主谓关系就不要输出。
6. chunk_id 必须照抄输入标记。
7. 每次最多输出 {max_entities} 个实体、{max_relationships} 条关系，
   只保留最明确、置信度最高的结果；
   没有明确关系时返回空数组。
8. {direction_rule}

输出结构：
{
  "entities": [
    {"name":"原文实体名","type":"实体类型","description":"简短说明","chunk_id":"原 chunk_id"}
  ],
  "relationships": [
    {"source":"实体名","target":"实体名","type":"USES","description":"关系说明",
     "confidence":0.91,"evidence":"支持关系的原文片段","chunk_id":"原 chunk_id"}
  ]
}
实体类型只能取：system, service, component, api, data, model, algorithm,
standard, organization, person, concept, technology。"""


def _bounded_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


def _system_prompt() -> str:
    max_entities = _bounded_int(
        settings.graph_semantic_max_entities_per_batch,
        1,
        100,
    )
    max_relationships = _bounded_int(
        settings.graph_semantic_max_relationships_per_batch,
        1,
        200,
    )
    endpoint_rule = (
        "evidence 必须直接出现 source 和 target；不要使用代词或跨句猜测。"
        if settings.graph_semantic_require_endpoints_in_evidence
        else "evidence 必须直接支持关系，不得使用跨 chunk 猜测。"
    )
    direction_rules = []
    if settings.graph_semantic_validate_part_of_direction:
        direction_rules.append("A PART_OF B 表示 A 是 B 的一部分")
    if settings.graph_semantic_reject_negative_stores:
        direction_rules.append(
            "A STORES_IN B 表示 A 被存储在 B 中；“删除/清理 B”不是 STORES_IN B"
        )
    direction_rule = "；".join(direction_rules) or "关系方向必须与原文一致"
    return (
        _SYSTEM_PROMPT_TEMPLATE.replace("{endpoint_rule}", endpoint_rule)
        .replace("{max_entities}", str(max_entities))
        .replace("{max_relationships}", str(max_relationships))
        .replace("{direction_rule}", direction_rule)
    )


def _value(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, "")


def _compact(value: str) -> str:
    return " ".join(str(value or "").split())


def _relation_evidence_is_consistent(
    *,
    source: str,
    target: str,
    relation_type: str,
    evidence: str,
    require_endpoints: bool,
    reject_negative_stores: bool,
    validate_part_of_direction: bool,
) -> bool:
    folded = evidence.casefold()
    source_folded = source.casefold()
    target_folded = target.casefold()
    if require_endpoints and (
        source_folded not in folded or target_folded not in folded
    ):
        return False
    if reject_negative_stores and relation_type == "STORES_IN" and any(
        term in folded
        for term in (
            "delete",
            "remove",
            "redact",
            "strip",
            "删除",
            "移除",
            "清理",
            "脱敏",
        )
    ):
        return False
    if validate_part_of_direction and relation_type == "PART_OF":
        # "A's B" / "A 的 B" normally means B is part of A. Reject the
        # common reversed extraction A -[PART_OF]-> B.
        reverse_patterns = (
            rf"{re.escape(source_folded)}\s*的\s*{re.escape(target_folded)}",
            rf"{re.escape(source_folded)}['’]s\s+{re.escape(target_folded)}",
        )
        if any(re.search(pattern, folded) for pattern in reverse_patterns):
            return False
    return True


def _extract_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*\})\s*```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise TypeError("semantic graph output must be a JSON object")
    return parsed


def validate_extraction(
    raw: str | dict,
    *,
    chunks: list[dict],
    min_confidence: float,
    max_entity_name_chars: int | None = None,
    max_description_chars: int | None = None,
    min_evidence_chars: int | None = None,
    max_evidence_chars: int | None = None,
    require_endpoints_in_evidence: bool | None = None,
    reject_negative_stores: bool | None = None,
    validate_part_of_direction: bool | None = None,
) -> dict:
    """Reject hallucinated entities/relations and retain source evidence."""
    max_name_chars = _bounded_int(
        max_entity_name_chars
        if max_entity_name_chars is not None
        else settings.graph_semantic_max_entity_name_chars,
        2,
        200,
    )
    max_description = _bounded_int(
        max_description_chars
        if max_description_chars is not None
        else settings.graph_semantic_max_description_chars,
        0,
        1000,
    )
    min_evidence = _bounded_int(
        min_evidence_chars
        if min_evidence_chars is not None
        else settings.graph_semantic_min_evidence_chars,
        1,
        200,
    )
    max_evidence = _bounded_int(
        max_evidence_chars
        if max_evidence_chars is not None
        else settings.graph_semantic_max_evidence_chars,
        min_evidence,
        2000,
    )
    require_endpoints = (
        require_endpoints_in_evidence
        if require_endpoints_in_evidence is not None
        else settings.graph_semantic_require_endpoints_in_evidence
    )
    reject_negative = (
        reject_negative_stores
        if reject_negative_stores is not None
        else settings.graph_semantic_reject_negative_stores
    )
    validate_part_of = (
        validate_part_of_direction
        if validate_part_of_direction is not None
        else settings.graph_semantic_validate_part_of_direction
    )
    payload = _extract_json_object(raw) if isinstance(raw, str) else raw
    chunk_text = {
        str(_value(chunk, "chunk_id") or _value(chunk, "id")): str(
            _value(chunk, "content") or ""
        )
        for chunk in chunks
    }
    entity_rows: list[dict] = []
    entities_by_name: dict[str, dict] = {}
    rejected = 0

    for item in payload.get("entities") or []:
        if not isinstance(item, dict):
            rejected += 1
            continue
        name = _compact(item.get("name", ""))[:max_name_chars]
        entity_type = str(item.get("type") or "concept").strip().casefold()
        requested_chunk = str(item.get("chunk_id") or "")
        candidate_ids = (
            [requested_chunk] if requested_chunk in chunk_text else list(chunk_text)
        )
        matched_chunk = next(
            (
                chunk_id
                for chunk_id in candidate_ids
                if name and name.casefold() in chunk_text[chunk_id].casefold()
            ),
            "",
        )
        key = name.casefold()
        if (
            not matched_chunk
            or len(name) < 2
            or entity_type not in ENTITY_TYPES
            or key in entities_by_name
        ):
            rejected += 1
            continue
        row = {
            "name": name,
            "type": entity_type,
            "description": _compact(item.get("description", ""))[
                :max_description
            ],
            "chunk_id": matched_chunk,
            "content": chunk_text[matched_chunk],
        }
        entities_by_name[key] = row
        entity_rows.append(row)

    relation_rows: list[dict] = []
    seen_relations: set[tuple[str, str, str, str]] = set()
    for item in payload.get("relationships") or []:
        if not isinstance(item, dict):
            rejected += 1
            continue
        source_key = _compact(item.get("source", "")).casefold()
        target_key = _compact(item.get("target", "")).casefold()
        relation_type = str(item.get("type") or "").strip().upper()
        chunk_id = str(item.get("chunk_id") or "")
        evidence = _compact(item.get("evidence", ""))[:max_evidence]
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = -1.0
        compact_chunk = _compact(chunk_text.get(chunk_id, ""))
        signature = (source_key, target_key, relation_type, chunk_id)
        if (
            source_key == target_key
            or source_key not in entities_by_name
            or target_key not in entities_by_name
            or relation_type not in RELATION_TYPES
            or confidence < min_confidence
            or confidence > 1.0
            or len(evidence) < min_evidence
            or evidence.casefold() not in compact_chunk.casefold()
            or not _relation_evidence_is_consistent(
                source=entities_by_name.get(source_key, {}).get("name", ""),
                target=entities_by_name.get(target_key, {}).get("name", ""),
                relation_type=relation_type,
                evidence=evidence,
                require_endpoints=require_endpoints,
                reject_negative_stores=reject_negative,
                validate_part_of_direction=validate_part_of,
            )
            or signature in seen_relations
        ):
            rejected += 1
            continue
        seen_relations.add(signature)
        relation_rows.append(
            {
                "source": entities_by_name[source_key]["name"],
                "target": entities_by_name[target_key]["name"],
                "type": relation_type,
                "description": _compact(item.get("description", ""))[
                    :max_description
                ],
                "confidence": confidence,
                "evidence": evidence,
                "chunk_id": chunk_id,
            }
        )

    return {
        "entities": entity_rows,
        "relationships": relation_rows,
        "rejected": rejected,
    }


def _prompt_batches(
    chunks: list[dict],
    *,
    max_chars: int,
    max_chunks: int,
) -> list[tuple[str, list[dict]]]:
    batches: list[tuple[str, list[dict]]] = []
    selected: list[dict] = []
    parts: list[str] = []
    used = 0

    def flush() -> None:
        nonlocal selected, parts, used
        if selected:
            batches.append(("\n\n".join(parts), selected))
        selected, parts, used = [], [], 0

    for chunk in chunks:
        chunk_id = str(_value(chunk, "chunk_id") or _value(chunk, "id"))
        content = str(_value(chunk, "content") or "")
        if not chunk_id or not content:
            continue
        if selected and (
            len(selected) >= max_chunks or used + len(content) > max_chars
        ):
            flush()
        content = content[:max_chars]
        selected.append(
            {
                "chunk_id": chunk_id,
                "doc_id": str(_value(chunk, "doc_id") or ""),
                "tenant_id": str(_value(chunk, "tenant_id") or "default"),
                "content": content,
            }
        )
        parts.append(f"[chunk_id={chunk_id}]\n{content}")
        used += len(content)
    flush()
    return batches


def _provider_candidates() -> list[tuple[str, str, str]]:
    """Return model/provider triples without ever mixing keys and endpoints."""
    candidates = [
        (
            settings.graph_semantic_api_key,
            settings.graph_semantic_base_url or settings.llm_base_url,
            settings.graph_semantic_model,
        ),
        (
            settings.bailian_api_key,
            settings.llm_base_url,
            settings.graph_semantic_model,
        ),
        (
            settings.hyde_api_key,
            settings.hyde_base_url or settings.llm_base_url,
            settings.hyde_model,
        ),
        (
            settings.mem0_api_key,
            settings.mem0_base_url or settings.llm_base_url,
            settings.mem0_model,
        ),
        (
            settings.reasoning_api_key,
            settings.reasoning_base_url or settings.llm_base_url,
            settings.reasoning_model,
        ),
        (
            (
                settings.siliconflow_api_key
                if "siliconflow" in settings.llm_base_url.casefold()
                else ""
            ),
            settings.llm_base_url,
            settings.graph_semantic_model,
        ),
    ]
    output: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for api_key, base_url, model in candidates:
        candidate = (
            str(api_key or ""),
            str(base_url or "").rstrip("/"),
            str(model or ""),
        )
        if not all(candidate) or candidate in seen:
            continue
        seen.add(candidate)
        output.append(candidate)
    return output


async def extract_and_store_semantic_graph(
    *,
    kb_id: str,
    collection_name: str,
    chunks: list[dict],
    llm_client: Any | None = None,
    store: Neo4jGraphStore | None = None,
) -> dict:
    """Extract one document's semantic graph and replace its prior LLM edges."""
    if not settings.graph_semantic_enabled:
        return {"status": "disabled", "entities": 0, "edges": 0, "rejected": 0}
    if not chunks:
        return {"status": "empty", "entities": 0, "edges": 0, "rejected": 0}

    provider_candidates = _provider_candidates()
    if llm_client is None and not provider_candidates:
        return {"status": "no_api_key", "entities": 0, "edges": 0, "rejected": 0}

    batches = _prompt_batches(
        chunks,
        max_chars=max(1000, settings.graph_semantic_max_input_chars),
        max_chunks=max(1, min(settings.graph_semantic_batch_chunks, 50)),
    )
    if not batches:
        return {"status": "empty", "entities": 0, "edges": 0, "rejected": 0}

    attempts: list[tuple[Any, str]]
    if llm_client is not None:
        attempts = [
            (llm_client, str(getattr(llm_client, "model", "injected-test-model")))
        ]
    else:
        attempts = [
            (
                LLMClient(api_key=api_key, base_url=base_url, model=model),
                model,
            )
            for api_key, base_url, model in provider_candidates
        ]
        if not settings.graph_semantic_provider_fallback_enabled:
            attempts = attempts[:1]

    selected = [chunk for _text, batch in batches for chunk in batch]
    combined_entities: dict[str, dict] = {}
    combined_relationships: list[dict] = []
    seen_relationships: set[tuple[str, str, str, str]] = set()
    rejected = 0
    used_models: list[str] = []
    disabled_attempts: set[int] = set()

    for batch_index, (text, batch) in enumerate(batches):
        messages = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": f"请抽取以下文档：\n\n{text}"},
        ]
        validated = None
        last_error: Exception | None = None
        for attempt_index, (client, model) in enumerate(attempts):
            if attempt_index in disabled_attempts:
                continue
            tokens: list[str] = []
            try:
                async with asyncio.timeout(
                    max(1.0, settings.graph_semantic_timeout_sec)
                ):
                    async for token in client.chat_stream(
                        messages,
                        temperature=max(
                            0.0,
                            min(settings.graph_semantic_temperature, 2.0),
                        ),
                        max_tokens=_bounded_int(
                            settings.graph_semantic_max_tokens,
                            256,
                            8192,
                        ),
                    ):
                        if not token.startswith("__THINK__"):
                            tokens.append(token)
                validated = validate_extraction(
                    "".join(tokens),
                    chunks=batch,
                    min_confidence=max(
                        0.0,
                        min(settings.graph_semantic_min_confidence, 1.0),
                    ),
                )
                used_models.append(model)
                for relation in validated["relationships"]:
                    relation["model"] = model
                break
            except Exception as exc:  # noqa: BLE001 - bounded provider fallback
                last_error = exc
                status_code = getattr(
                    getattr(exc, "response", None),
                    "status_code",
                    None,
                )
                if status_code in {401, 403, 404}:
                    disabled_attempts.add(attempt_index)
                _log.warning(
                    "semantic_graph_provider_failed",
                    kb_id=kb_id,
                    batch=batch_index + 1,
                    model=model,
                    error_type=type(exc).__name__,
                )
        if validated is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("semantic graph extraction has no usable provider")

        rejected += int(validated.get("rejected") or 0)
        for entity in validated["entities"]:
            combined_entities.setdefault(entity["name"].casefold(), entity)
        for relation in validated["relationships"]:
            signature = (
                relation["source"].casefold(),
                relation["target"].casefold(),
                relation["type"],
                relation["chunk_id"],
            )
            if signature not in seen_relationships:
                seen_relationships.add(signature)
                combined_relationships.append(relation)

    validated = {
        "entities": list(combined_entities.values()),
        "relationships": combined_relationships,
        "rejected": rejected,
    }

    result = await (store or Neo4jGraphStore()).upsert_semantic_graph(
        kb_id=kb_id,
        collection_name=collection_name,
        chunks=selected,
        extraction=validated,
        model=",".join(dict.fromkeys(used_models)),
        prompt_version=settings.graph_semantic_prompt_version,
    )
    summary = {
        "status": "complete",
        "entities": result["nodes"],
        "edges": result["edges"],
        "rejected": validated["rejected"],
    }
    _log.info("semantic_graph_extraction_complete", kb_id=kb_id, **summary)
    return summary
