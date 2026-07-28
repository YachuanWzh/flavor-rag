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

    LightRAG remains the semantic graph enhancer.  This store guarantees that
    Graph RAG still has real, permission-resolvable chunk evidence when the
    optional extraction model is unavailable.
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
            return {"nodes": 0, "edges": 0}

        doc_ids = {str(self._value(chunk, "doc_id")) for chunk in chunk_list}
        entity_rows: list[dict] = []
        relation_rows: list[dict] = []
        seen_entities: set[str] = set()
        seen_relations: set[tuple[str, str, str]] = set()

        for chunk in chunk_list:
            chunk_id = str(self._value(chunk, "id"))
            doc_id = str(self._value(chunk, "doc_id"))
            content = str(self._value(chunk, "content") or "")
            names = self.extract_entities(content)
            entity_ids: list[str] = []
            for name, entity_type in names:
                entity_id = self._entity_id(kb_id, doc_id, name)
                entity_ids.append(entity_id)
                if entity_id in seen_entities:
                    continue
                seen_entities.add(entity_id)
                entity_rows.append(
                    {
                        "id": entity_id,
                        "name": name,
                        "type": entity_type,
                        "description": self._description_for(name, content),
                        "content": content,
                        "kb_id": kb_id,
                        "collection_name": collection_name,
                        "doc_id": doc_id,
                        "chunk_id": chunk_id,
                    }
                )

            # Connect nearby entities from the same chunk.  A cap keeps dense
            # API/code chunks from creating a quadratic explosion.
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
        try:
            async with driver.session() as session:
                await session.run(
                    "CREATE INDEX flavor_entity_kb IF NOT EXISTS "
                    "FOR (n:FlavorEntity) ON (n.kb_id)"
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
                            entity.entity_type = row.type,
                            entity.description = row.description,
                            entity.content = row.content,
                            entity.kb_id = row.kb_id,
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
            return {"nodes": len(entity_rows), "edges": len(relation_rows)}
        finally:
            pass

    async def delete_document(self, *, kb_id: str, doc_id: str) -> None:
        driver = self._driver()
        try:
            async with driver.session() as session:
                await session.run(
                    "MATCH (n:FlavorEntity {kb_id: $kb_id, doc_id: $doc_id}) "
                    "DETACH DELETE n",
                    kb_id=kb_id,
                    doc_id=doc_id,
                )
        finally:
            pass

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
        try:
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
        finally:
            pass

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
        kb_id: str,
        entity: str = "*",
        limit: int = 80,
    ) -> dict:
        driver = self._driver()
        try:
            async with driver.session() as session:
                node_result = await session.run(
                    """
                    MATCH (node:FlavorEntity {kb_id: $kb_id})
                    WHERE $entity = '*' OR toLower(node.name) CONTAINS toLower($entity)
                    OPTIONAL MATCH (node)-[relation:FLAVOR_RELATED]-(neighbor:FlavorEntity {
                        kb_id: $kb_id
                    })
                    WITH node, count(relation) AS degree
                    ORDER BY degree DESC, node.name
                    LIMIT $limit
                    RETURN node.id AS id,
                           node.name AS name,
                           node.entity_type AS type,
                           node.description AS description,
                           node.doc_id AS document_id
                    """,
                    kb_id=kb_id,
                    entity=entity or "*",
                    limit=max(1, min(limit, 500)),
                )
                nodes = await node_result.data()
                node_ids = [str(row["id"]) for row in nodes]
                edge_result = await session.run(
                    """
                    MATCH (source:FlavorEntity)-[relation:FLAVOR_RELATED]->(target:FlavorEntity)
                    WHERE source.id IN $node_ids AND target.id IN $node_ids
                    RETURN source.id AS source,
                           target.id AS target,
                           relation.chunk_id AS chunk_id,
                           relation.label AS label
                    LIMIT 1000
                    """,
                    node_ids=node_ids,
                )
                raw_edges = await edge_result.data()
        finally:
            pass

        return {
            "nodes": [
                {
                    "id": str(row["id"]),
                    "name": str(row.get("name") or row["id"]),
                    "type": str(row.get("type") or ""),
                    "description": str(row.get("description") or ""),
                    "documentId": str(row.get("document_id") or ""),
                }
                for row in nodes
            ],
            "edges": [
                {
                    "id": f"{row['source']}:{row['target']}:{row.get('chunk_id', '')}",
                    "source": str(row["source"]),
                    "target": str(row["target"]),
                    "label": str(row.get("label") or "相关"),
                    "description": "",
                }
                for row in raw_edges
            ],
            "truncated": len(nodes) >= limit,
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
            normalized = " ".join(name.split()).strip(" -:：,，。")
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
    def _description_for(name: str, content: str) -> str:
        compact = " ".join(content.split())
        index = compact.casefold().find(name.casefold())
        start = max(0, index - 100) if index >= 0 else 0
        return compact[start : start + 360]
