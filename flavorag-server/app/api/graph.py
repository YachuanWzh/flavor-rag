"""Authenticated Graph RAG capabilities and knowledge-graph views."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config.settings import settings
from app.database.session import get_db
from app.models import User
from app.rag.graph.lightrag_client import LightRAGClient
from app.rag.graph.neo4j_store import Neo4jGraphStore
from app.security.access import Permission
from app.security.service import principal_from_user, require_kb

router = APIRouter(prefix="/api/rag/v3", tags=["graph-rag"])


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
        },
    }


@router.get("/graph")
async def graph_view(
    kb_id: str = Query(..., description="知识库ID"),
    entity: str = Query("*", description="中心实体；* 表示知识库全图"),
    depth: int = Query(2, ge=1, le=5),
    limit: int = Query(80, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return a permission-scoped graph for the chat-side visualisation."""
    kb = await require_kb(
        db,
        principal_from_user(user),
        kb_id,
        Permission.READ,
    )
    native_graph = await Neo4jGraphStore().fetch_graph(
        kb_id=kb.id,
        entity=entity,
        limit=limit,
    )
    try:
        enriched_graph = await LightRAGClient().fetch_graph(
            entity=entity,
            depth=depth,
            limit=limit,
            scope_tokens=(kb.id, kb.collection_name),
            enabled=True,
        )
    except Exception:
        enriched_graph = {"nodes": [], "edges": [], "truncated": False}

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
