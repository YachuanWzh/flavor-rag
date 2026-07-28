"""Deterministic Neo4j graph fallback for reliable Graph RAG."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from itertools import combinations
from typing import Any

from neo4j import AsyncGraphDatabase

from app.config.settings import settings
from app.rag.search.base import SearchResult

_driver_instance = None

# These tokens remain useful inside a document graph, but are too generic to
# explain a relationship between two knowledge bases.
_CROSS_KB_STOP_NAMES = frozenset(
    {
        "and", "any", "api", "array", "block", "bool", "boolean", "class", "code",
        "create", "data", "delete", "description", "dict", "div", "else",
        "error", "false", "float", "for", "from", "get", "id", "if",
        "apikey", "authorization", "http", "https", "input", "int", "item",
        "json", "key", "list", "main", "map", "name",
        "node", "none", "not", "null", "object", "option", "output", "parse",
        "post", "put", "request", "response", "result", "return", "set",
        "span", "string", "system", "table", "tbody", "td", "text", "then",
        "password", "secret", "token", "true", "type", "update", "url",
        "user", "value", "void", "where", "with", "xml", "yaml",
    }
)


async def close_neo4j_driver() -> None:
    global _driver_instance
    if _driver_instance is not None:
        await _driver_instance.close()
        _driver_instance = None


class Neo4jGraphStore:
    """Build and query a lightweight entity/co-occurrence graph.

    LightRAG remains the semantic graph enhancer. This store guarantees that
    Graph RAG still has permission-resolvable chunk evidence when the optional
    extraction model is unavailable. It also creates deterministic same-entity
    bridges between knowledge bases in the same tenant.
    """

    def _driver(self):
        global _driver_instance
        if _driver_instance is None:
            _driver_instance = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
        return _driver_instance

    async def upsert_chunks(
        self,
        *,
        kb_id: str,
        collection_name: str,
        chunks: Iterable[Any],
    ) -> dict:
        chunk_list = list(chunks)
        if not chunk_list:
            return {"nodes": 0, "edges": 0, "crossEdges": 0}

        doc_ids = {str(self._value(chunk, "doc_id")) for chunk in chunk_list}
        entity_rows: list[dict] = []
        relation_rows: list[dict] = []
        seen_entities: set[str] = set()
        seen_relations: set[tuple[str, str, str]] = set()

        for chunk in chunk_list:
            chunk_id = str(
                self._value(chunk, "id") or self._value(chunk, "chunk_id")
            )
            doc_id = str(self._value(chunk, "doc_id"))
            tenant_id = str(self._value(chunk, "tenant_id") or "default")
            content = str(self._value(chunk, "content") or "")
            entity_ids: list[str] = []
            for name, entity_type in self.extract_entities(content):
                entity_id = self._entity_id(kb_id, doc_id, name)
                entity_ids.append(entity_id)
                if entity_id in seen_entities:
                    continue
                seen_entities.add(entity_id)
                entity_rows.append(
                    {
                        "id": entity_id,
                        "name": name,
                        "normalized_name": self._normalized_name(name),
                        "cross_linkable": self._is_cross_kb_candidate(name),
                        "type": entity_type,
                        "description": self._description_for(name, content),
                        "content": content,
                        "kb_id": kb_id,
                        "tenant_id": tenant_id,
                        "collection_name": collection_name,
                        "doc_id": doc_id,
                        "chunk_id": chunk_id,
                    }
                )

            # Bound within-chunk density to avoid a quadratic explosion.
            for index, source in enumerate(entity_ids[:8]):
                for target in entity_ids[index + 1 : 8]:
                    left, right = sorted((source, target))
                    signature = (left, right, chunk_id)
                    if signature in seen_relations:
                        continue
                    seen_relations.add(signature)
                    relation_rows.append(
                        {
                            "source": left,
                            "target": right,
                            "chunk_id": chunk_id,
                            "doc_id": doc_id,
                            "kb_id": kb_id,
                        }
                    )

        driver = self._driver()
        async with driver.session() as session:
            await session.run(
                "CREATE INDEX flavor_entity_kb IF NOT EXISTS "
                "FOR (n:FlavorEntity) ON (n.kb_id)"
            )
            await session.run(
                "CREATE INDEX flavor_entity_tenant_name IF NOT EXISTS "
                "FOR (n:FlavorEntity) ON (n.tenant_id, n.normalized_name)"
            )
            old_key_result = await session.run(
                """
                MATCH (node:FlavorEntity)
                WHERE node.kb_id = $kb_id AND node.doc_id IN $doc_ids
                RETURN DISTINCT node.tenant_id AS tenant_id,
                                node.normalized_name AS normalized_name
                """,
                kb_id=kb_id,
                doc_ids=list(doc_ids),
            )
            old_keys = await old_key_result.data()
            await session.run(
                "MATCH (n:FlavorEntity) "
                "WHERE n.kb_id = $kb_id AND n.doc_id IN $doc_ids "
                "DETACH DELETE n",
                kb_id=kb_id,
                doc_ids=list(doc_ids),
            )
            if entity_rows:
                await session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (entity:FlavorEntity {id: row.id})
                    SET entity.name = row.name,
                        entity.normalized_name = row.normalized_name,
                        entity.cross_linkable = row.cross_linkable,
                        entity.entity_type = row.type,
                        entity.description = row.description,
                        entity.content = row.content,
                        entity.kb_id = row.kb_id,
                        entity.tenant_id = row.tenant_id,
                        entity.collection_name = row.collection_name,
                        entity.doc_id = row.doc_id,
                        entity.chunk_id = row.chunk_id,
                        entity.deterministic_extracted = true,
                        entity.updated_at = datetime()
                    """,
                    rows=entity_rows,
                )
            if relation_rows:
                await session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (source:FlavorEntity {id: row.source})
                    MATCH (target:FlavorEntity {id: row.target})
                    MERGE (source)-[relation:FLAVOR_RELATED {
                        chunk_id: row.chunk_id
                    }]->(target)
                    SET relation.label = '共现',
                        relation.doc_id = row.doc_id,
                        relation.kb_id = row.kb_id,
                        relation.weight = 1.0
                    """,
                    rows=relation_rows,
                )
            affected_keys = {
                (
                    str(row.get("tenant_id") or ""),
                    str(row.get("normalized_name") or ""),
                )
                for row in [*old_keys, *entity_rows]
                if row.get("tenant_id") and row.get("normalized_name")
            }
            if affected_keys:
                await self._rebuild_cross_kb_relations(
                    session,
                    [
                        {"tenant_id": tenant_id, "normalized_name": normalized_name}
                        for tenant_id, normalized_name in sorted(affected_keys)
                    ],
                )
        return {
            "nodes": len(entity_rows),
            "edges": len(relation_rows),
            # The exact MERGE count is intentionally not inferred client-side.
            "crossEdges": None,
        }

    async def upsert_semantic_graph(
        self,
        *,
        kb_id: str,
        collection_name: str,
        chunks: Iterable[Any],
        extraction: dict,
        model: str,
        prompt_version: str,
    ) -> dict:
        """Replace one document's evidence-grounded semantic graph.

        Deterministic nodes survive a semantic-only refresh. Semantic-only
        nodes and all old semantic relations for the document are replaced,
        making retries and document updates idempotent.
        """
        chunk_list = list(chunks)
        if not chunk_list:
            return {"nodes": 0, "edges": 0}
        doc_ids = {
            str(self._value(chunk, "doc_id") or "")
            for chunk in chunk_list
            if self._value(chunk, "doc_id")
        }
        if not doc_ids:
            # The prompt-size adapter intentionally keeps only fields needed by
            # the model. Recover the single document id from the extraction
            # caller's original chunk payload when available.
            doc_ids = {
                str(self._value(chunk, "document_id") or "")
                for chunk in chunk_list
                if self._value(chunk, "document_id")
            }
        if len(doc_ids) != 1:
            raise ValueError("semantic graph upsert requires exactly one document")
        doc_id = next(iter(doc_ids))
        tenant_id = str(self._value(chunk_list[0], "tenant_id") or "default")
        content_by_chunk = {
            str(self._value(chunk, "chunk_id") or self._value(chunk, "id")): str(
                self._value(chunk, "content") or ""
            )
            for chunk in chunk_list
        }

        entity_rows: list[dict] = []
        entity_ids: dict[str, str] = {}
        for entity in extraction.get("entities") or []:
            name = str(entity.get("name") or "")
            chunk_id = str(entity.get("chunk_id") or "")
            if not name or chunk_id not in content_by_chunk:
                continue
            entity_id = self._entity_id(kb_id, doc_id, name)
            entity_ids[name.casefold()] = entity_id
            entity_rows.append(
                {
                    "id": entity_id,
                    "name": name,
                    "normalized_name": self._normalized_name(name),
                    "cross_linkable": self._is_cross_kb_candidate(name),
                    "type": str(entity.get("type") or "concept"),
                    "description": str(entity.get("description") or ""),
                    "content": content_by_chunk[chunk_id],
                    "kb_id": kb_id,
                    "tenant_id": tenant_id,
                    "collection_name": collection_name,
                    "doc_id": doc_id,
                    "chunk_id": chunk_id,
                }
            )

        relation_rows: list[dict] = []
        for relation in extraction.get("relationships") or []:
            source = entity_ids.get(str(relation.get("source") or "").casefold())
            target = entity_ids.get(str(relation.get("target") or "").casefold())
            if not source or not target:
                continue
            evidence = str(relation.get("evidence") or "")
            signature = ":".join(
                (
                    kb_id,
                    doc_id,
                    str(relation.get("chunk_id") or ""),
                    source,
                    target,
                    str(relation.get("type") or ""),
                    evidence,
                )
            )
            relation_rows.append(
                {
                    "evidence_id": hashlib.sha256(signature.encode()).hexdigest()[:32],
                    "source": source,
                    "target": target,
                    "chunk_id": str(relation.get("chunk_id") or ""),
                    "doc_id": doc_id,
                    "kb_id": kb_id,
                    "tenant_id": tenant_id,
                    "relation_type": str(relation.get("type") or ""),
                    "description": str(relation.get("description") or ""),
                    "confidence": float(relation.get("confidence") or 0.0),
                    "evidence": evidence,
                    "model": str(relation.get("model") or model),
                    "prompt_version": prompt_version,
                }
            )

        driver = self._driver()
        async with driver.session() as session:
            old_key_result = await session.run(
                """
                MATCH (node:FlavorEntity {kb_id: $kb_id, doc_id: $doc_id})
                WHERE node.semantic_extracted = true
                RETURN DISTINCT node.tenant_id AS tenant_id,
                                node.normalized_name AS normalized_name
                """,
                kb_id=kb_id,
                doc_id=doc_id,
            )
            old_keys = await old_key_result.data()
            await session.run(
                """
                MATCH (:FlavorEntity)-[relation:SEMANTIC_RELATED {
                    kb_id: $kb_id, doc_id: $doc_id
                }]->(:FlavorEntity)
                DELETE relation
                """,
                kb_id=kb_id,
                doc_id=doc_id,
            )
            await session.run(
                """
                MATCH (node:FlavorEntity {kb_id: $kb_id, doc_id: $doc_id})
                WHERE node.semantic_extracted = true
                  AND coalesce(node.deterministic_extracted, false) = false
                DETACH DELETE node
                """,
                kb_id=kb_id,
                doc_id=doc_id,
            )
            if entity_rows:
                await session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (entity:FlavorEntity {id: row.id})
                    ON CREATE SET entity.deterministic_extracted = false
                    ON MATCH SET entity.deterministic_extracted =
                        coalesce(entity.deterministic_extracted, true)
                    SET entity.name = row.name,
                        entity.normalized_name = row.normalized_name,
                        entity.cross_linkable = row.cross_linkable,
                        entity.entity_type = row.type,
                        entity.description = row.description,
                        entity.content = row.content,
                        entity.kb_id = row.kb_id,
                        entity.tenant_id = row.tenant_id,
                        entity.collection_name = row.collection_name,
                        entity.doc_id = row.doc_id,
                        entity.chunk_id = row.chunk_id,
                        entity.semantic_extracted = true,
                        entity.updated_at = datetime()
                    """,
                    rows=entity_rows,
                )
            if relation_rows:
                await session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (source:FlavorEntity {id: row.source})
                    MATCH (target:FlavorEntity {id: row.target})
                    MERGE (source)-[relation:SEMANTIC_RELATED {
                        evidence_id: row.evidence_id
                    }]->(target)
                    SET relation.label = row.relation_type,
                        relation.relation_type = row.relation_type,
                        relation.description = row.description,
                        relation.confidence = row.confidence,
                        relation.evidence = row.evidence,
                        relation.chunk_id = row.chunk_id,
                        relation.doc_id = row.doc_id,
                        relation.kb_id = row.kb_id,
                        relation.tenant_id = row.tenant_id,
                        relation.model = row.model,
                        relation.prompt_version = row.prompt_version,
                        relation.cross_kb = false,
                        relation.updated_at = datetime()
                    """,
                    rows=relation_rows,
                )
            affected_keys = {
                (
                    str(row.get("tenant_id") or ""),
                    str(row.get("normalized_name") or ""),
                )
                for row in [*old_keys, *entity_rows]
                if row.get("tenant_id") and row.get("normalized_name")
            }
            if affected_keys:
                await self._rebuild_cross_kb_relations(
                    session,
                    [
                        {"tenant_id": tenant, "normalized_name": name}
                        for tenant, name in sorted(affected_keys)
                    ],
                )
        return {"nodes": len(entity_rows), "edges": len(relation_rows)}

    async def _rebuild_cross_kb_relations(
        self,
        session,
        keys: list[dict],
    ) -> None:
        """Rebuild one representative bridge per shared entity and KB pair."""
        if not keys:
            return
        await session.run(
            """
            UNWIND $keys AS key
            MATCH (node:FlavorEntity {
                tenant_id: key.tenant_id,
                normalized_name: key.normalized_name
            })-[relation:CROSS_KB_RELATED]-()
            WITH DISTINCT relation
            DELETE relation
            """,
            keys=keys,
        )
        await session.run(
            """
            UNWIND $keys AS key
            MATCH (candidate:FlavorEntity {
                tenant_id: key.tenant_id,
                normalized_name: key.normalized_name
            })
            WHERE candidate.cross_linkable = true
            OPTIONAL MATCH (candidate)-[local:FLAVOR_RELATED]-()
            WITH key, candidate, count(local) AS local_degree
            ORDER BY key.tenant_id, key.normalized_name,
                     candidate.kb_id, local_degree DESC, candidate.id
            WITH key, candidate.kb_id AS kb_id,
                 head(collect(candidate)) AS representative
            WITH key, collect(representative) AS representatives
            WHERE size(representatives) > 1
            UNWIND range(0, size(representatives) - 2) AS left_index
            UNWIND range(left_index + 1, size(representatives) - 1) AS right_index
            WITH key,
                 representatives[left_index] AS left,
                 representatives[right_index] AS right
            WITH key,
                 CASE WHEN left.id < right.id THEN left ELSE right END AS source,
                 CASE WHEN left.id < right.id THEN right ELSE left END AS target
            MERGE (source)-[relation:CROSS_KB_RELATED]->(target)
            SET relation.label = '跨库同名实体',
                relation.cross_kb = true,
                relation.match_method = 'normalized_exact_match',
                relation.confidence = 1.0,
                relation.tenant_id = key.tenant_id,
                relation.normalized_name = key.normalized_name,
                relation.updated_at = datetime()
            """,
            keys=keys,
        )

    async def backfill_cross_kb_relations(
        self,
        *,
        kb_tenants: dict[str, str],
        apply: bool = False,
        batch_size: int = 500,
    ) -> dict:
        """Backfill legacy metadata and rebuild deterministic cross-KB bridges."""
        mapping = {
            str(kb_id): str(tenant_id)
            for kb_id, tenant_id in kb_tenants.items()
            if kb_id and tenant_id
        }
        empty = {
            "knowledgeBases": 0,
            "nodesScanned": 0,
            "nodesUpdated": 0,
            "sharedNames": 0,
            "crossEdges": 0,
            "applied": False,
        }
        if not mapping:
            return empty

        driver = self._driver()
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (node:FlavorEntity)
                WHERE node.kb_id IN $kb_ids
                OPTIONAL MATCH (node)-[local:FLAVOR_RELATED]-()
                RETURN node.id AS id,
                       node.name AS name,
                       node.kb_id AS kb_id,
                       node.tenant_id AS tenant_id,
                       node.normalized_name AS normalized_name,
                       node.cross_linkable AS cross_linkable,
                       count(local) AS local_degree
                """,
                kb_ids=sorted(mapping),
            )
            nodes = await result.data()

            update_rows: list[dict] = []
            representatives: dict[
                tuple[str, str], dict[str, tuple[int, str]]
            ] = {}
            changed = 0
            for node in nodes:
                kb_id = str(node.get("kb_id") or "")
                name = str(node.get("name") or "")
                tenant_id = mapping.get(kb_id, "")
                normalized_name = self._normalized_name(name)
                cross_linkable = self._is_cross_kb_candidate(name)
                update_rows.append(
                    {
                        "id": str(node.get("id") or ""),
                        "tenant_id": tenant_id,
                        "normalized_name": normalized_name,
                        "cross_linkable": cross_linkable,
                    }
                )
                if (
                    str(node.get("tenant_id") or "") != tenant_id
                    or str(node.get("normalized_name") or "") != normalized_name
                    or node.get("cross_linkable") is None
                    or bool(node.get("cross_linkable")) != cross_linkable
                ):
                    changed += 1
                if not cross_linkable:
                    continue
                key = (tenant_id, normalized_name)
                by_kb = representatives.setdefault(key, {})
                candidate = (
                    int(node.get("local_degree") or 0),
                    str(node.get("id") or ""),
                )
                current = by_kb.get(kb_id)
                if (
                    current is None
                    or candidate[0] > current[0]
                    or (candidate[0] == current[0] and candidate[1] < current[1])
                ):
                    by_kb[kb_id] = candidate

            relation_rows: list[dict] = []
            shared_names = 0
            for (tenant_id, normalized_name), by_kb in representatives.items():
                if len(by_kb) < 2:
                    continue
                shared_names += 1
                representative_ids = [
                    value[1] for _kb_id, value in sorted(by_kb.items())
                ]
                for left, right in combinations(representative_ids, 2):
                    source, target = sorted((left, right))
                    relation_rows.append(
                        {
                            "source": source,
                            "target": target,
                            "tenant_id": tenant_id,
                            "normalized_name": normalized_name,
                        }
                    )

            summary = {
                "knowledgeBases": len(mapping),
                "nodesScanned": len(nodes),
                "nodesUpdated": changed,
                "sharedNames": shared_names,
                "crossEdges": len(relation_rows),
                "applied": bool(apply),
            }
            if not apply:
                return summary

            safe_batch_size = max(1, min(int(batch_size), 5000))
            for offset in range(0, len(update_rows), safe_batch_size):
                await session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (node:FlavorEntity {id: row.id})
                    SET node.tenant_id = row.tenant_id,
                        node.normalized_name = row.normalized_name,
                        node.cross_linkable = row.cross_linkable,
                        node.cross_metadata_updated_at = datetime()
                    """,
                    rows=update_rows[offset : offset + safe_batch_size],
                )

            await session.run(
                """
                MATCH (left:FlavorEntity)-[relation:CROSS_KB_RELATED]->(right)
                WHERE left.kb_id IN $kb_ids OR right.kb_id IN $kb_ids
                DELETE relation
                """,
                kb_ids=sorted(mapping),
            )
            for offset in range(0, len(relation_rows), safe_batch_size):
                await session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (source:FlavorEntity {id: row.source})
                    MATCH (target:FlavorEntity {id: row.target})
                    MERGE (source)-[relation:CROSS_KB_RELATED]->(target)
                    SET relation.label = '跨库同名实体',
                        relation.cross_kb = true,
                        relation.match_method = 'normalized_exact_match',
                        relation.confidence = 1.0,
                        relation.tenant_id = row.tenant_id,
                        relation.normalized_name = row.normalized_name,
                        relation.updated_at = datetime()
                    """,
                    rows=relation_rows[offset : offset + safe_batch_size],
                )
            return summary

    async def delete_document(self, *, kb_id: str, doc_id: str) -> None:
        driver = self._driver()
        async with driver.session() as session:
            key_result = await session.run(
                """
                MATCH (node:FlavorEntity {kb_id: $kb_id, doc_id: $doc_id})
                RETURN DISTINCT node.tenant_id AS tenant_id,
                                node.normalized_name AS normalized_name
                """,
                kb_id=kb_id,
                doc_id=doc_id,
            )
            old_keys = await key_result.data()
            await session.run(
                "MATCH (n:FlavorEntity {kb_id: $kb_id, doc_id: $doc_id}) "
                "DETACH DELETE n",
                kb_id=kb_id,
                doc_id=doc_id,
            )
            await self._rebuild_cross_kb_relations(
                session,
                [
                    {
                        "tenant_id": str(row.get("tenant_id") or ""),
                        "normalized_name": str(row.get("normalized_name") or ""),
                    }
                    for row in old_keys
                    if row.get("tenant_id") and row.get("normalized_name")
                ],
            )

    async def search(
        self,
        query: str,
        *,
        kb_id: str,
        top_k: int = 8,
    ) -> list[SearchResult]:
        terms = self.query_terms(query)
        if not kb_id or not terms:
            return []
        driver = self._driver()
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (entity:FlavorEntity {kb_id: $kb_id})
                WHERE any(
                    term IN $terms
                    WHERE toLower(entity.name) CONTAINS term
                       OR toLower(entity.description) CONTAINS term
                )
                WITH entity,
                     CASE
                       WHEN any(term IN $terms WHERE toLower(entity.name) CONTAINS term)
                       THEN 1.0 ELSE 0.72
                     END AS score
                RETURN entity.chunk_id AS chunk_id,
                       entity.doc_id AS doc_id,
                       entity.content AS content,
                       entity.name AS entity_name,
                       score
                ORDER BY score DESC, size(entity.name) ASC
                LIMIT $limit
                """,
                kb_id=kb_id,
                terms=terms,
                limit=max(1, min(top_k, 50)),
            )
            rows = await result.data()

        output: list[SearchResult] = []
        seen_chunks: set[str] = set()
        for row in rows:
            chunk_id = str(row.get("chunk_id") or "")
            content = str(row.get("content") or "")
            if not chunk_id or not content or chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            output.append(
                SearchResult(
                    chunk_id=chunk_id,
                    doc_id=str(row.get("doc_id") or ""),
                    content=content,
                    score=float(row.get("score") or 0.0),
                    metadata={"graphEntity": str(row.get("entity_name") or "")},
                )
            )
        return output

    async def fetch_graph(
        self,
        *,
        kb_id: str | None = None,
        kb_ids: Iterable[str] | None = None,
        entity: str = "*",
        limit: int = 200,
        per_type: bool = False,
    ) -> dict:
        allowed_kb_ids = list(dict.fromkeys(kb_ids or ([kb_id] if kb_id else [])))
        if not allowed_kb_ids:
            return {"nodes": [], "edges": [], "truncated": False}

        capped_limit = max(1, min(limit, 200))
        driver = self._driver()
        async with driver.session() as session:
            if per_type:
                node_query = """
                MATCH (node:FlavorEntity)
                WHERE node.kb_id IN $kb_ids
                  AND ($entity = '*' OR toLower(node.name) CONTAINS toLower($entity))
                OPTIONAL MATCH (node)-[relation:FLAVOR_RELATED|SEMANTIC_RELATED|CROSS_KB_RELATED]-(neighbor:FlavorEntity)
                WHERE neighbor.kb_id IN $kb_ids
                WITH node, count(relation) AS degree,
                     CASE WHEN node.semantic_extracted = true THEN 1 ELSE 0 END
                     AS semantic_rank
                WITH coalesce(toLower(trim(node.entity_type)), 'unclassified') AS entity_type,
                     node, degree, semantic_rank
                ORDER BY entity_type, semantic_rank DESC, degree DESC, node.name
                WITH entity_type, collect(node)[..$limit] AS typed_nodes
                UNWIND typed_nodes AS node
                RETURN node.id AS id,
                       node.name AS name,
                       node.entity_type AS type,
                       node.description AS description,
                       node.doc_id AS document_id,
                       node.kb_id AS knowledge_base_id
                """
            else:
                node_query = """
                MATCH (node:FlavorEntity)
                WHERE node.kb_id IN $kb_ids
                  AND ($entity = '*' OR toLower(node.name) CONTAINS toLower($entity))
                OPTIONAL MATCH (node)-[relation:FLAVOR_RELATED|SEMANTIC_RELATED|CROSS_KB_RELATED]-(neighbor:FlavorEntity)
                WHERE neighbor.kb_id IN $kb_ids
                WITH node, count(relation) AS degree,
                     CASE WHEN node.semantic_extracted = true THEN 1 ELSE 0 END
                     AS semantic_rank
                ORDER BY semantic_rank DESC, degree DESC, node.name
                LIMIT $limit
                RETURN node.id AS id,
                       node.name AS name,
                       node.entity_type AS type,
                       node.description AS description,
                       node.doc_id AS document_id,
                       node.kb_id AS knowledge_base_id
                """
            node_result = await session.run(
                node_query,
                kb_ids=allowed_kb_ids,
                entity=entity or "*",
                limit=capped_limit + 1,
            )
            raw_nodes = await node_result.data()
            if per_type:
                type_counts: dict[str, int] = {}
                nodes = []
                for row in raw_nodes:
                    type_key = str(row.get("type") or "").strip().casefold()
                    type_key = type_key or "unclassified"
                    type_counts[type_key] = type_counts.get(type_key, 0) + 1
                    if type_counts[type_key] <= capped_limit:
                        nodes.append(row)
                truncated_by_type = {
                    type_key: True
                    for type_key, count in type_counts.items()
                    if count > capped_limit
                }
                truncated = bool(truncated_by_type)
            else:
                truncated = len(raw_nodes) > capped_limit
                truncated_by_type = {}
                nodes = raw_nodes[:capped_limit]
            node_ids = [str(row["id"]) for row in nodes]

            edge_result = await session.run(
                """
                MATCH (source:FlavorEntity)-[relation:FLAVOR_RELATED|SEMANTIC_RELATED|CROSS_KB_RELATED]->(target:FlavorEntity)
                WHERE source.id IN $node_ids AND target.id IN $node_ids
                RETURN source.id AS source,
                       target.id AS target,
                       relation.chunk_id AS chunk_id,
                       relation.label AS label,
                       coalesce(relation.relation_type, type(relation)) AS relation_type,
                       relation.description AS description,
                       relation.confidence AS confidence,
                       relation.evidence AS evidence,
                       relation.model AS model,
                       coalesce(relation.cross_kb, false) AS cross_kb
                UNION
                MATCH (source:FlavorEntity), (target:FlavorEntity)
                WHERE source.id IN $node_ids
                  AND target.id IN $node_ids
                  AND source.id < target.id
                  AND source.tenant_id = target.tenant_id
                  AND source.kb_id <> target.kb_id
                  AND source.cross_linkable = true
                  AND target.cross_linkable = true
                  AND source.normalized_name = target.normalized_name
                  AND NOT (source)-[:CROSS_KB_RELATED]-(target)
                RETURN source.id AS source,
                       target.id AS target,
                       null AS chunk_id,
                       '跨库同名实体' AS label,
                       'CROSS_KB_RELATED' AS relation_type,
                       null AS description,
                       1.0 AS confidence,
                       null AS evidence,
                       null AS model,
                       true AS cross_kb
                LIMIT 2000
                """,
                node_ids=node_ids,
            )
            raw_edges = await edge_result.data()

        return {
            "nodes": [
                {
                    "id": str(row["id"]),
                    "name": str(row.get("name") or row["id"]),
                    "type": str(row.get("type") or ""),
                    "description": str(row.get("description") or ""),
                    "documentId": str(row.get("document_id") or ""),
                    "knowledgeBaseId": str(row.get("knowledge_base_id") or ""),
                }
                for row in nodes
            ],
            "edges": [
                {
                    "id": (
                        f"{row['source']}:{row['target']}:"
                        f"{row.get('relation_type', '')}:{row.get('chunk_id', '')}"
                    ),
                    "source": str(row["source"]),
                    "target": str(row["target"]),
                    "label": str(row.get("label") or "相关"),
                    "description": str(row.get("description") or ""),
                    "type": str(row.get("relation_type") or ""),
                    "confidence": (
                        float(row["confidence"])
                        if row.get("confidence") is not None
                        else None
                    ),
                    "evidence": str(row.get("evidence") or ""),
                    "model": str(row.get("model") or ""),
                    "crossKnowledgeBase": bool(row.get("cross_kb")),
                }
                for row in raw_edges
            ],
            "truncated": truncated,
            "truncatedByType": truncated_by_type,
        }

    @classmethod
    def extract_entities(cls, content: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for heading in re.findall(r"(?m)^#{1,6}\s+(.{2,80})$", content):
            clean = re.sub(r"^\d+(?:\.\d+)*[、.\s-]*", "", heading).strip()
            if clean:
                candidates.append((clean, "section"))
        for identifier in re.findall(r"`([^`\n]{2,80})`", content):
            candidates.append((identifier.strip(), "identifier"))
        for identifier in re.findall(
            r"\b(?:[A-Z][A-Za-z0-9]+){1,}[A-Za-z0-9]*\b|"
            r"\b[A-Za-z][A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\b",
            content,
        ):
            candidates.append((identifier.strip(), "identifier"))

        output: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name, entity_type in candidates:
            normalized = " ".join(name.split()).strip(" -:：，。")
            key = normalized.casefold()
            if (
                len(normalized) < 2
                or len(normalized) > 80
                or key in seen
                or key in {"typescript", "javascript", "string", "true", "false"}
            ):
                continue
            seen.add(key)
            output.append((normalized, entity_type))
            if len(output) >= 12:
                break
        return output

    @staticmethod
    def query_terms(query: str) -> list[str]:
        terms = re.findall(
            r"[A-Za-z][A-Za-z0-9_.:/-]{1,}|[\u4e00-\u9fff]{2,}",
            query,
        )
        seen: list[str] = []
        for term in terms:
            value = term.casefold().strip()
            if value and value not in seen:
                seen.append(value)
        return seen[:12]

    @staticmethod
    def _value(item: Any, key: str) -> Any:
        return item.get(key) if isinstance(item, dict) else getattr(item, key, "")

    @staticmethod
    def _entity_id(kb_id: str, doc_id: str, name: str) -> str:
        digest = hashlib.sha256(
            f"{kb_id}:{doc_id}:{name.casefold()}".encode()
        ).hexdigest()[:24]
        return f"flavor:{digest}"

    @staticmethod
    def _normalized_name(name: str) -> str:
        return re.sub(r"[\W_]+", "", name.casefold(), flags=re.UNICODE)

    @classmethod
    def _is_cross_kb_candidate(cls, name: str) -> bool:
        normalized = cls._normalized_name(name)
        if not normalized or normalized.isdigit():
            return False
        minimum_length = 2 if re.search(r"[\u4e00-\u9fff]", normalized) else 3
        return (
            len(normalized) >= minimum_length
            and normalized not in _CROSS_KB_STOP_NAMES
        )

    @staticmethod
    def _description_for(name: str, content: str) -> str:
        compact = " ".join(content.split())
        index = compact.casefold().find(name.casefold())
        start = max(0, index - 100) if index >= 0 else 0
        return compact[start : start + 360]
