"""LightRAG HTTP client used by retrieval, ingestion, and graph visualisation."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import httpx

from app.config.settings import settings


class LightRAGClient:
    """Small compatibility layer around the LightRAG 1.5 REST API.

    ``enabled`` can be supplied per call.  This is important for chat: the
    server setting is the default, while a user may explicitly enable or
    disable Graph RAG for one request from the UI.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.lightrag_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.lightrag_api_key

    @staticmethod
    def _disabled(enabled: bool | None = None) -> bool:
        return not (settings.graph_enabled if enabled is None else enabled)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    # ---- document ingestion -------------------------------------------------

    async def insert_document(
        self,
        kb_id: str,
        content: str,
        *,
        doc_id: str = "",
        collection_name: str = "",
        enabled: bool | None = None,
    ) -> dict:
        """Insert text using LightRAG's current ``/documents/text`` contract."""
        if self._disabled(enabled):
            return {"disabled": True}

        namespace = collection_name or kb_id
        file_source = "_".join(part for part in (namespace, doc_id) if part)
        payload: dict[str, Any] = {"text": content}
        if file_source:
            payload["file_source"] = file_source

        async with httpx.AsyncClient(
            timeout=120.0, headers=self._headers, trust_env=False
        ) as client:
            response = await client.post(
                f"{self.base_url}/documents/text",
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def insert_documents_batch(
        self,
        kb_id: str,
        contents: list[dict],
        *,
        collection_name: str = "",
        enabled: bool | None = None,
    ) -> list[dict]:
        """Group chunks by document and enqueue one graph extraction per document."""
        if self._disabled(enabled):
            return [{"disabled": True} for _ in contents]

        grouped: dict[str, list[str]] = {}
        for item in contents:
            text = str(item.get("content") or "").strip()
            if not text:
                continue
            grouped.setdefault(str(item.get("doc_id") or ""), []).append(text)

        results: list[dict] = []
        for doc_id, chunks in grouped.items():
            results.append(
                await self.insert_document(
                    kb_id,
                    "\n\n".join(chunks),
                    doc_id=doc_id,
                    collection_name=collection_name,
                    enabled=True,
                )
            )
        return results

    async def delete_document(
        self,
        kb_id: str,
        doc_id: str,
        *,
        enabled: bool | None = None,
    ) -> dict:
        """Delete all LightRAG documents whose file path belongs to ``doc_id``."""
        if self._disabled(enabled):
            return {"disabled": True}

        async with httpx.AsyncClient(
            timeout=30.0, headers=self._headers, trust_env=False
        ) as client:
            listing = await client.get(f"{self.base_url}/documents")
            listing.raise_for_status()
            payload = listing.json()
            matches: list[str] = []
            statuses = payload.get("statuses", {}) if isinstance(payload, dict) else {}
            for documents in statuses.values():
                if not isinstance(documents, list):
                    continue
                for document in documents:
                    file_path = str(document.get("file_path") or "")
                    if doc_id and doc_id in file_path and document.get("id"):
                        matches.append(str(document["id"]))
            if not matches:
                return {"deleted": True, "count": 0}
            response = await client.request(
                "DELETE",
                f"{self.base_url}/documents/delete_document",
                json={"doc_ids": matches},
            )
            response.raise_for_status()
            body = response.json() if response.content else {}
            return {"deleted": True, "count": len(matches), **body}

    # ---- graph retrieval ----------------------------------------------------

    async def query_graph(
        self,
        query: str,
        mode: str = "mix",
        top_k: int = 5,
        *,
        kb_id: str = "",
        collection_name: str = "",
        enabled: bool | None = None,
    ) -> dict:
        """Return LightRAG references normalized as ordinary retrieval hits."""
        if self._disabled(enabled):
            return {"disabled": True, "results": []}

        async with httpx.AsyncClient(
            timeout=60.0, headers=self._headers, trust_env=False
        ) as client:
            response = await client.post(
                f"{self.base_url}/query",
                json={
                    "query": query,
                    "mode": mode,
                    "top_k": top_k,
                    "only_need_context": True,
                    "include_references": True,
                    "include_chunk_content": True,
                },
            )
            response.raise_for_status()
            payload = response.json()

        scope_tokens = [token for token in (collection_name, kb_id) if token]
        return {
            "results": self._normalise_query_response(
                payload,
                scope_tokens=scope_tokens,
                top_k=top_k,
            ),
            "response": payload.get("response", "") if isinstance(payload, dict) else "",
        }

    async def fetch_graph(
        self,
        *,
        entity: str = "*",
        depth: int = 2,
        limit: int = 200,
        scope_tokens: Iterable[str] = (),
        enabled: bool | None = None,
    ) -> dict:
        """Fetch and normalize a visualisable subgraph."""
        if self._disabled(enabled):
            return {"nodes": [], "edges": [], "truncated": False, "disabled": True}

        async with httpx.AsyncClient(
            timeout=30.0, headers=self._headers, trust_env=False
        ) as client:
            response = await client.get(
                f"{self.base_url}/graphs",
                params={
                    "label": entity or "*",
                    "max_depth": max(1, min(depth, 5)),
                    "max_nodes": max(1, min(limit, 500)),
                },
            )
            response.raise_for_status()
            return self._normalise_graph_response(
                response.json(),
                scope_tokens=[token for token in scope_tokens if token],
                limit=limit,
            )

    async def search_labels(
        self,
        keyword: str = "",
        *,
        limit: int = 30,
        enabled: bool | None = None,
    ) -> list[str]:
        if self._disabled(enabled):
            return []
        path = "/graph/label/search" if keyword.strip() else "/graph/label/popular"
        params = (
            {"q": keyword.strip(), "limit": max(1, min(limit, 100))}
            if keyword.strip()
            else {"limit": max(1, min(limit, 300))}
        )
        async with httpx.AsyncClient(
            timeout=15.0, headers=self._headers, trust_env=False
        ) as client:
            response = await client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            return []
        return [
            self._repair_text(
                str(item if isinstance(item, str) else item.get("label") or item.get("name") or "")
            )
            for item in payload
            if item
        ]

    async def health(self, *, enabled: bool | None = None) -> dict:
        if self._disabled(enabled):
            return {"disabled": True}
        try:
            async with httpx.AsyncClient(
                timeout=5.0, headers=self._headers, trust_env=False
            ) as client:
                response = await client.get(f"{self.base_url}/health")
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc)}

    # ---- response mapping ---------------------------------------------------

    @classmethod
    def _normalise_query_response(
        cls,
        payload: Any,
        *,
        scope_tokens: list[str],
        top_k: int,
    ) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        references = payload.get("references")
        if not isinstance(references, list):
            return []

        results: list[dict] = []
        for reference in references:
            if not isinstance(reference, dict):
                continue
            file_path = str(reference.get("file_path") or "")
            if scope_tokens and not cls._matches_scope(file_path, scope_tokens):
                continue
            raw_content = reference.get("content")
            parts = raw_content if isinstance(raw_content, list) else [raw_content]
            for part in parts:
                content = cls._repair_text(str(part or "").strip())
                if not content:
                    continue
                rank = len(results)
                results.append(
                    {
                        # The relational metadata resolver can still match the
                        # exact content hash when LightRAG uses an internal id.
                        "chunk_id": str(reference.get("chunk_id") or reference.get("reference_id") or ""),
                        "doc_id": cls._doc_id_from_path(file_path, scope_tokens),
                        "doc_name": cls._repair_text(file_path),
                        "content": content,
                        "score": 1.0 / (rank + 1),
                        "channel": "graph",
                    }
                )
                if len(results) >= top_k:
                    return results
        return results

    @classmethod
    def _normalise_graph_response(
        cls,
        payload: Any,
        *,
        scope_tokens: list[str],
        limit: int,
    ) -> dict:
        if not isinstance(payload, dict):
            return {"nodes": [], "edges": [], "truncated": False}

        kept_ids: set[str] = set()
        nodes: list[dict] = []
        for raw in payload.get("nodes", []):
            if not isinstance(raw, dict):
                continue
            props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
            file_path = str(props.get("file_path") or "")
            if scope_tokens and not cls._matches_scope(file_path, scope_tokens):
                continue
            node_id = str(raw.get("id") or props.get("entity_id") or "")
            if not node_id:
                continue
            labels = raw.get("labels") if isinstance(raw.get("labels"), list) else []
            name = str(props.get("entity_id") or (labels[0] if labels else node_id))
            kept_ids.add(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "name": cls._repair_text(name),
                    "type": cls._repair_text(str(props.get("entity_type") or "")),
                    "description": cls._clean_merged(str(props.get("description") or ""), "\n"),
                    "documentId": cls._doc_id_from_path(file_path, scope_tokens),
                }
            )
            if len(nodes) >= max(1, limit):
                break

        edges: list[dict] = []
        for raw in payload.get("edges", []):
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source") or "")
            target = str(raw.get("target") or "")
            if source not in kept_ids or target not in kept_ids:
                continue
            props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
            label = props.get("keywords") or raw.get("type") or ""
            edges.append(
                {
                    "id": str(raw.get("id") or f"{source}-{target}"),
                    "source": source,
                    "target": target,
                    "label": cls._clean_merged(str(label), " / "),
                    "description": cls._clean_merged(str(props.get("description") or ""), "\n"),
                }
            )

        return {
            "nodes": nodes,
            "edges": edges,
            "truncated": bool(payload.get("is_truncated")),
        }

    @staticmethod
    def _matches_scope(file_path: str, tokens: Iterable[str]) -> bool:
        return bool(file_path) and any(token and token in file_path for token in tokens)

    @staticmethod
    def _doc_id_from_path(file_path: str, tokens: Iterable[str]) -> str:
        for token in sorted((item for item in tokens if item), key=len, reverse=True):
            match = re.search(re.escape(token) + r"[_:/-]+([^/\\]+)", file_path)
            if match:
                return match.group(1).rsplit(".", 1)[0]
        return ""

    @classmethod
    def _clean_merged(cls, raw: str, joiner: str) -> str:
        values: list[str] = []
        for part in raw.split("<SEP>"):
            value = cls._repair_text(part.strip())
            if value and value not in values:
                values.append(value)
        return joiner.join(values)

    @staticmethod
    def _repair_text(value: str) -> str:
        """Repair the common UTF-8-as-Latin-1 mojibake found in old graphs."""
        if not value or not any(marker in value for marker in ("Ã", "Â", "æ", "å", "ç")):
            return value
        try:
            repaired = value.encode("latin1").decode("utf-8")
            return repaired if repaired else value
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value
