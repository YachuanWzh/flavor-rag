"""Authenticated Graph RAG capabilities and knowledge-graph views."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.session import get_db
from app.models import KnowledgeBase, User
from app.rag.graph.lightrag_client import LightRAGClient
from app.rag.graph.neo4j_store import Neo4jGraphStore
from app.security.access import Permission
from app.security.service import kb_access_predicate, principal_from_user, require_kb

router = APIRouter(prefix="/api/rag/v3", tags=["graph-rag"])
_log = get_logger("flavorag.api.graph")


@router.get("/capabilities")
async def rag_capabilities(
    user: User = Depends(get_current_user),
):
    """Return UI-safe feature defaults and live Graph RAG availability."""
    graph_health = await LightRAGClient().health(enabled=True)
    graph_available = graph_health.get("status") not in {
        "unreachable",
        "error",
    }
    return {
        "code": "0",
        "message": "success",
        "data": {
            "agenticRag": {
                "available": True,
                "defaultEnabled": settings.agentic_rag_enabled,
                "maxSteps": settings.agent_max_steps,
            },
            "graphRag": {
                "available": graph_available,
                "defaultEnabled": settings.graph_enabled,
                "status": graph_health.get("status", "unknown"),
            },
            "hyde": {
                "available": bool(settings.hyde_model),
                "defaultEnabled": settings.hyde_enabled,
                "model": settings.hyde_model,
            },
        },
    }


@router.get("/graph")
async def graph_view(
    kb_id: str = Query(..., description="知识库ID"),
    entity: str = Query("*", description="中心实体；* 表示知识库全图"),
    depth: int = Query(2, ge=1, le=5),
    limit: int = Query(200, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return a permission-scoped graph for the chat-side visualisation."""
    principal = principal_from_user(user)
    if kb_id == "*":
        result = await db.execute(
            select(KnowledgeBase)
            .where(kb_access_predicate(principal, Permission.READ))
            .order_by(KnowledgeBase.name, KnowledgeBase.id)
        )
        knowledge_bases = list(result.scalars().all())
    else:
        knowledge_bases = [
            await require_kb(db, principal, kb_id, Permission.READ)
        ]
    kb_ids = [kb.id for kb in knowledge_bases]
    kb_names = {kb.id: getattr(kb, "name", "") for kb in knowledge_bases}
    try:
        native_graph = await Neo4jGraphStore().fetch_graph(
            kb_ids=kb_ids,
            entity=entity,
            limit=limit,
        )
    except Exception as exc:
        _log.warning(
            "neo4j_graph_view_failed",
            error_type=type(exc).__name__,
            kb_id=kb_id,
        )
        native_graph = {"nodes": [], "edges": [], "truncated": False}
    enriched_graphs = []
    if knowledge_bases:
        async def fetch_enriched(kb):
            try:
                graph = await LightRAGClient().fetch_graph(
                    entity=entity,
                    depth=depth,
                    limit=limit,
                    scope_tokens=(
                        kb.id,
                        getattr(kb, "active_collection_name", None)
                        or kb.collection_name,
                    ),
                    enabled=True,
                )
                if kb_id == "*":
                    for node in graph.get("nodes", []):
                        node["knowledgeBaseId"] = kb.id
                        node["knowledgeBaseName"] = getattr(kb, "name", "")
                return graph
            except Exception:
                return {"nodes": [], "edges": [], "truncated": False}

        enriched_graphs = await asyncio.gather(
            *(fetch_enriched(kb) for kb in knowledge_bases)
        )
    enriched_graph = {
        "nodes": [
            node for graph in enriched_graphs for node in graph.get("nodes", [])
        ],
        "edges": [
            edge for graph in enriched_graphs for edge in graph.get("edges", [])
        ],
        "truncated": any(graph.get("truncated") for graph in enriched_graphs),
    }

    for node in native_graph.get("nodes", []):
        node_kb_id = str(node.get("knowledgeBaseId") or "")
        node["knowledgeBaseName"] = kb_names.get(node_kb_id, "")

    nodes_by_id = {
        str(node["id"]): node
        for node in [*native_graph["nodes"], *enriched_graph["nodes"]]
    }
    edges_by_id = {
        str(edge["id"]): edge
        for edge in [*native_graph["edges"], *enriched_graph["edges"]]
        if str(edge["source"]) in nodes_by_id
        and str(edge["target"]) in nodes_by_id
    }
    visible_nodes = list(nodes_by_id.values())[:limit]
    visible_node_ids = {str(node["id"]) for node in visible_nodes}
    visible_edges = [
        edge
        for edge in edges_by_id.values()
        if str(edge["source"]) in visible_node_ids
        and str(edge["target"]) in visible_node_ids
    ]
    graph = {
        "nodes": visible_nodes,
        "edges": visible_edges,
        "truncated": bool(
            native_graph.get("truncated") or enriched_graph.get("truncated")
            or len(nodes_by_id) > limit
        ),
    }
    return {
        "code": "0",
        "message": "success",
        "data": graph,
    }


@router.get("/graph/labels")
async def graph_labels(
    keyword: str = Query(""),
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    """Search graph labels for the visualisation's entity focus control."""
    labels = await LightRAGClient().search_labels(
        keyword,
        limit=limit,
        enabled=True,
    )
    return {"code": "0", "message": "success", "data": labels}
