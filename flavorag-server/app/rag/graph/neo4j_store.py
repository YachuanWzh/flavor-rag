"""Deterministic Neo4j graph fallback for reliable Graph RAG."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from neo4j import AsyncGraphDatabase

from app.config.settings import settings
from app.rag.search.base import SearchResult

_driver_instance = None


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
                        entity.entity_type = row.type,
                        entity.description = row.description,
                        entity.content = row.content,
                        entity.kb_id = row.kb_id,
                        entity.tenant_id = row.tenant_id,
                        entity.collection_name = row.collection_name,
                        entity.doc_id = row.doc_id,
                        entity.chunk_id = row.chunk_id,
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
            if entity_rows:
                await session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (entity:FlavorEntity {id: row.id})
                    MATCH (other:FlavorEntity {
                        tenant_id: row.tenant_id,
                        normalized_name: row.normalized_name
                    })
                    WHERE row.normalized_name <> ''
                      AND other.kb_id <> row.kb_id
                    FOREACH (_ IN CASE WHEN entity.id < other.id THEN [1] ELSE [] END |
                        MERGE (entity)-[forward:CROSS_KB_RELATED]->(other)
                        SET forward.label = '跨库同名实体',
                            forward.cross_kb = true,
                            forward.updated_at = datetime()
                    )
                    FOREACH (_ IN CASE WHEN entity.id > other.id THEN [1] ELSE [] END |
                        MERGE (other)-[backward:CROSS_KB_RELATED]->(entity)
                        SET backward.label = '跨库同名实体',
                            backward.cross_kb = true,
                            backward.updated_at = datetime()
                    )
                    """,
                    rows=entity_rows,
                )
        return {
            "nodes": len(entity_rows),
            "edges": len(relation_rows),
            # The exact MERGE count is intentionally not inferred client-side.
            "crossEdges": None,
        }

    async def delete_document(self, *, kb_id: str, doc_id: str) -> None:
        driver = self._driver()
        async with driver.session() as session:
            await session.run(
                "MATCH (n:FlavorEntity {kb_id: $kb_id, doc_id: $doc_id}) "
                "DETACH DELETE n",
                kb_id=kb_id,
                doc_id=doc_id,
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
    ) -> dict:
        allowed_kb_ids = list(dict.fromkeys(kb_ids or ([kb_id] if kb_id else [])))
        if not allowed_kb_ids:
            return {"nodes": [], "edges": [], "truncated": False}

        capped_limit = max(1, min(limit, 200))
        driver = self._driver()
        async with driver.session() as session:
            node_result = await session.run(
                """
                MATCH (node:FlavorEntity)
                WHERE node.kb_id IN $kb_ids
                  AND ($entity = '*' OR toLower(node.name) CONTAINS toLower($entity))
                OPTIONAL MATCH (node)-[relation:FLAVOR_RELATED|CROSS_KB_RELATED]-(neighbor:FlavorEntity)
                WHERE neighbor.kb_id IN $kb_ids
                WITH node, count(relation) AS degree
                ORDER BY degree DESC, node.name
                LIMIT $limit
                RETURN node.id AS id,
                       node.name AS name,
                       node.entity_type AS type,
                       node.description AS description,
                       node.doc_id AS document_id,
                       node.kb_id AS knowledge_base_id
                """,
                kb_ids=allowed_kb_ids,
                entity=entity or "*",
                limit=capped_limit + 1,
            )
            raw_nodes = await node_result.data()
            truncated = len(raw_nodes) > capped_limit
            nodes = raw_nodes[:capped_limit]
            node_ids = [str(row["id"]) for row in nodes]

            edge_result = await session.run(
                """
                MATCH (source:FlavorEntity)-[relation:FLAVOR_RELATED|CROSS_KB_RELATED]->(target:FlavorEntity)
                WHERE source.id IN $node_ids AND target.id IN $node_ids
                RETURN source.id AS source,
                       target.id AS target,
                       relation.chunk_id AS chunk_id,
                       relation.label AS label,
                       type(relation) AS relation_type,
                       coalesce(relation.cross_kb, false) AS cross_kb
                UNION
                MATCH (source:FlavorEntity), (target:FlavorEntity)
                WHERE source.id IN $node_ids
                  AND target.id IN $node_ids
                  AND source.id < target.id
                  AND source.tenant_id = target.tenant_id
                  AND source.kb_id <> target.kb_id
                  AND toLower(trim(source.name)) = toLower(trim(target.name))
                  AND NOT (source)-[:CROSS_KB_RELATED]-(target)
                RETURN source.id AS source,
                       target.id AS target,
                       null AS chunk_id,
                       '跨库同名实体' AS label,
                       'CROSS_KB_RELATED' AS relation_type,
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
                    "description": "",
                    "type": str(row.get("relation_type") or ""),
                    "crossKnowledgeBase": bool(row.get("cross_kb")),
                }
                for row in raw_edges
            ],
            "truncated": truncated,
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

    @staticmethod
    def _description_for(name: str, content: str) -> str:
        compact = " ".join(content.split())
        index = compact.casefold().find(name.casefold())
        start = max(0, index - 100) if index >= 0 else 0
        return compact[start : start + 360]
