from __future__ import annotations

import json
from dataclasses import replace

from app.agent.controlled import AgentAction, ControlledAgent
from app.config.settings import settings
from app.database.session import engine
from app.rag.pipeline import (
    RAGContext,
    RAGPipeline,
    RAGResult,
    select_query_scopes,
)
from app.tools.registry import ToolRegistry
from app.tools.sql_tool import ReadOnlySQLTool
from app.tools.mcp_tool import ControlledMCPClient, MCPToolTarget
from app.agent.planner import plan_next_action


def _retrieval_tool_timeout_sec(*, enable_hyde: bool) -> float:
    """Allow the pipeline's bounded phases to finish before cancelling it."""
    understanding_timeout = settings.query_understanding_timeout_sec
    if enable_hyde and settings.hyde_enabled:
        understanding_timeout = max(
            understanding_timeout,
            settings.hyde_timeout_sec,
        )
    return max(
        float(settings.agent_tool_timeout_sec),
        understanding_timeout
        + settings.retrieval_total_timeout_ms / 1000
        + settings.reranker_timeout_sec
        + 5.0,
    )


class ControlledRAGAgent:
    """Bounded retrieve/tool/finish loop used when Agentic RAG is enabled."""

    def __init__(self, pipeline: RAGPipeline):
        self.pipeline = pipeline

    async def run(self, context: RAGContext) -> tuple[RAGResult, list[dict]]:
        context = replace(
            context,
            retrieval_scopes=select_query_scopes(
                context.question, context.retrieval_scopes
            ),
        )
        registry = ToolRegistry()
        retrieved: list[RAGResult] = []

        async def retrieve(arguments: dict, security_context: dict) -> dict:
            query = str(arguments.get("query") or context.question).strip()
            result = await self.pipeline.run(replace(context, question=query))
            retrieved.append(result)
            # Compute score diagnostics for planner decision-making
            scores = [
                float(c.get("score", 0))
                for c in result.context_chunks
                if c.get("score") is not None
            ]
            max_score = max(scores) if scores else 0.0
            avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
            return {
                "query": query,
                "answerable": result.answerable,
                "source_count": len(result.sources),
                "rejection_reason": result.rejection_reason,
                "channels": result.channel_statuses,
                "max_score": round(max_score, 4),
                "avg_score": avg_score,
                "subqueries_used": result.subqueries,
            }

        registry.register(
            "retrieve",
            retrieve,
            read_only=True,
            timeout_sec=_retrieval_tool_timeout_sec(
                enable_hyde=context.enable_hyde,
            ),
        )
        if settings.sql_tool_enabled:
            relations = {
                item.strip()
                for item in settings.sql_tool_allowed_relations.split(",")
                if item.strip()
            }
            if relations:
                registry.register(
                    "sql",
                    ReadOnlySQLTool(engine, allowed_relations=relations),
                    read_only=True,
                    timeout_sec=settings.agent_tool_timeout_sec,
                )
        if settings.mcp_tool_enabled:
            configured = json.loads(settings.mcp_tools_json or "{}")
            allowed = {
                item.strip()
                for item in settings.mcp_allowed_tools.split(",")
                if item.strip()
            }
            targets = {
                name: MCPToolTarget(
                    name=name,
                    endpoint=str(config["endpoint"]),
                    read_only=bool(config.get("read_only", True)),
                    timeout_sec=float(
                        config.get("timeout_sec", settings.agent_tool_timeout_sec)
                    ),
                )
                for name, config in configured.items()
                if name in allowed and isinstance(config, dict) and config.get("endpoint")
            }
            mcp = ControlledMCPClient(targets)
            for tool_name in targets:
                async def invoke_mcp(arguments, security_context, name=tool_name):
                    return await mcp.invoke(name, arguments, security_context)

                registry.register(
                    tool_name,
                    invoke_mcp,
                    read_only=targets[tool_name].read_only,
                    timeout_sec=targets[tool_name].timeout_sec,
                )

        async def planner(state: dict) -> AgentAction:
            if not state["steps"]:
                return AgentAction("retrieve", {"query": context.question})
            last_obs = state["steps"][-1].observation
            # Early stop: evidence is answerable
            if last_obs.get("answerable"):
                return AgentAction("finish", {"answer": "evidence_ready"})
            # Early stop: high confidence score even if not flagged answerable
            if last_obs.get("max_score", 0) >= 0.85 and last_obs.get("source_count", 0) >= 2:
                return AgentAction("finish", {"answer": "high_confidence_evidence"})
            # Early stop: multiple failed attempts with very low scores → knowledge gap
            if len(state["steps"]) >= 2:
                recent_scores = [
                    s.observation.get("max_score", 0) for s in state["steps"][-2:]
                ]
                if all(score < 0.15 for score in recent_scores):
                    return AgentAction("finish", {"answer": "knowledge_gap"})
            return await plan_next_action(
                question=context.question,
                steps=state["steps"],
                allowed_tools=list(registry.names),
            )

        agent = ControlledAgent(
            registry,
            planner=planner,
            max_steps=settings.agent_max_steps,
        )
        result = await agent.run(
            context.question,
            {
                "tenant_id": context.tenant_id,
                "department_id": context.department_id,
                "user_id": context.user_id,
            },
        )
        rag_result = next(
            (item for item in reversed(retrieved) if item.answerable),
            retrieved[-1] if retrieved else None,
        )
        if rag_result is None:
            raise RuntimeError(f"agent ended without retrieval: {result.status}")
        tool_evidence = [
            step
            for step in result.steps
            if step.action.tool != "retrieve" and step.observation
        ]
        if not rag_result.answerable and tool_evidence:
            chunks = list(rag_result.context_chunks)
            sources = list(rag_result.sources)
            for index, step in enumerate(tool_evidence):
                content = json.dumps(
                    step.observation,
                    ensure_ascii=False,
                    default=str,
                )
                chunks.append(
                    {
                        "content": content,
                        "chunk_id": f"tool:{step.action.tool}:{index}",
                        "score": 1.0,
                        "blockType": "TOOL_RESULT",
                        "pageStart": None,
                        "pageEnd": None,
                    }
                )
                sources.append(
                    {
                        "documentId": "",
                        "chunkId": f"tool:{step.action.tool}:{index}",
                        "docName": f"tool:{step.action.tool}",
                        "chunkIndex": index,
                        "content": content[:300],
                        "score": 1.0,
                        "blockType": "TOOL_RESULT",
                        "pageStart": None,
                        "pageEnd": None,
                        "bboxes": [],
                        "assets": [],
                    }
                )
            rag_result = replace(
                rag_result,
                context_chunks=chunks,
                sources=sources,
                answerable=True,
                rejection_reason=None,
            )
        steps = [
            {
                "tool": step.action.tool,
                "arguments": step.action.arguments,
                "observation": step.observation,
            }
            for step in result.steps
        ]
        return rag_result, steps
