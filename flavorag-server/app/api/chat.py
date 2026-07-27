"""SSE streaming chat API — GET /api/rag/v3/chat with trace + rate limit."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, Conversation, KnowledgeBase, gen_id
from app.rag.pipeline import RAGPipeline, RAGContext
from app.rag.trace import TraceLogger
from app.rag.rate_limiter import RateLimiter
from app.llm.client import get_llm_client
from app.services.chat_service import ChatService
from app.config.settings import settings
from app.rag.recommendations import recommend_questions
from app.security.access import Permission
from app.security.service import (
    kb_access_predicate,
    principal_from_user,
    require_kb,
)

router = APIRouter(prefix="/api/rag/v3", tags=["chat"])


@router.get("/chat")
async def chat(
    request: Request,
    question: str = Query(..., description="用户问题"),
    conversation_id: str | None = Query(None, description="会话ID"),
    kb_id: str | None = Query(None, description="知识库ID"),
    deep_thinking: bool = Query(False, description="启用深度思考"),
    agentic_rag: bool | None = Query(None, description="本次请求启用 Agentic RAG"),
    graph_rag: bool | None = Query(None, description="本次请求启用 Graph RAG"),
    neighbor_expansion: bool = Query(
        False, description="启用邻近chunk召回补偿"
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """SSE streaming RAG chat endpoint with trace + rate limiting."""
    t_total_start = time.time()
    effective_agentic_rag = (
        settings.agentic_rag_enabled if agentic_rag is None else agentic_rag
    )
    effective_graph_rag = (
        settings.graph_enabled if graph_rag is None else graph_rag
    )

    # Rate limiting
    if settings.rate_limit_enabled:
        rl = RateLimiter()
        client_ip = request.client.host if request.client else "unknown"
        if not await rl.check_user(user.id):
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        if not await rl.check_ip(client_ip):
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    # Trace
    trace = TraceLogger(db)
    trace_id = await trace.trace_query(
        query=question,
        user_id=user.id,
        conversation_id=conversation_id or "",
        tenant_id=user.tenant_id or "default",
        kb_id=kb_id,
    )

    rag_pipeline = RAGPipeline(trace_logger=trace)

    # Auto-create conversation if not provided
    if not conversation_id:
        conv = Conversation(
            id=gen_id(),
            conversation_id=gen_id(),
            user_id=user.id,
            tenant_id=user.tenant_id or "default",
            title=question[:30] + ("..." if len(question) > 30 else ""),
            last_time=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(conv)
        await db.flush()
        conversation_id = conv.id

    chat_service = ChatService(db, user_id=user.id)

    async def event_stream():
        try:
            # ─── TTFT optimization: immediate feedback ───
            if settings.ttft_early_feedback:
                yield (
                    "event: progress\ndata: "
                    + json.dumps({"stage": "thinking", "message": "正在理解您的问题..."}, ensure_ascii=False)
                    + "\n\n"
                )

            # 1. History
            history = await chat_service.get_context(conversation_id, turns=8)

            # 2. Save user message
            await chat_service.save_message(conversation_id=conversation_id, role="user", content=question)
            await db.flush()

            # 3. Resolve kb_id → collection_name (auto-select first KB if none given)
            resolved_kb_id = kb_id
            collection_name: str | None = None
            if not resolved_kb_id:
                # Auto-select first available knowledge base
                first_kb = await db.execute(
                    select(KnowledgeBase.id, KnowledgeBase.collection_name)
                    .where(
                        kb_access_predicate(
                            principal_from_user(user), Permission.READ
                        )
                    )
                    .limit(1)
                )
                first_row = first_kb.first()
                if first_row:
                    resolved_kb_id = first_row[0]
                    collection_name = first_row[1]
            else:
                kb = await require_kb(
                    db,
                    principal_from_user(user),
                    resolved_kb_id,
                    Permission.READ,
                )
                collection_name = kb.collection_name

            # 4. RAG retrieval
            if settings.ttft_early_feedback:
                yield (
                    "event: progress\ndata: "
                    + json.dumps({"stage": "retrieving", "message": "正在检索相关资料..."}, ensure_ascii=False)
                    + "\n\n"
                )
            ctx = RAGContext(
                question=question, conversation_id=conversation_id,
                kb_id=resolved_kb_id, collection_name=collection_name,
                history=history, deep_thinking=deep_thinking,
                graph_rag=effective_graph_rag,
                enable_neighbor_expansion=neighbor_expansion,
                trace_run_id=trace_id,
                user_id=user.id,
                tenant_id=user.tenant_id or "default",
                department_id=user.department_id or "",
                role=user.role,
            )
            agent_steps: list[dict] = []
            if effective_agentic_rag:
                from app.agent.rag_agent import ControlledRAGAgent

                agent_started = datetime.now(timezone.utc).replace(tzinfo=None)
                rag_result, agent_steps = await ControlledRAGAgent(rag_pipeline).run(ctx)
                agent_ended = datetime.now(timezone.utc).replace(tzinfo=None)
                await trace.trace_node(
                    trace_id,
                    "agent",
                    "controlled_agentic_rag",
                    agent_started,
                    agent_ended,
                    input_data={"max_steps": settings.agent_max_steps},
                    output_data={"steps": agent_steps},
                )
            else:
                rag_result = await rag_pipeline.run(ctx)

            # 5. Send meta
            meta = json.dumps(
                {
                    "conversationId": conversation_id,
                    "taskId": trace_id,
                    "modes": {
                        "agenticRag": effective_agentic_rag,
                        "graphRag": effective_graph_rag,
                        "neighborExpansion": neighbor_expansion,
                    },
                    "channels": rag_result.channel_statuses,
                    "queryUnderstanding": rag_result.intent,
                    "appliedMappings": [
                        {"source": m["source"], "target": m["target"], "type": m["type"]}
                        for m in rag_result.applied_mappings
                    ],
                }
            )
            yield f"event: meta\ndata: {meta}\n\n"
            if effective_agentic_rag:
                yield (
                    "event: agent\ndata: "
                    + json.dumps({"steps": agent_steps}, ensure_ascii=False)
                    + "\n\n"
                )

            if rag_result.response_mode == "guidance":
                guidance = rag_result.direct_response or "请补充你想查询的知识范围。"
                yield (
                    "event: message\ndata: "
                    + json.dumps({"type": "response", "delta": guidance}, ensure_ascii=False)
                    + "\n\n"
                )
                assistant_msg_id = await chat_service.save_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=guidance,
                    sources=[],
                    rag_modes={
                        "agenticRag": effective_agentic_rag,
                        "graphRag": effective_graph_rag,
                    },
                    retrieval_channels=rag_result.channel_statuses,
                )
                await trace.finalize(
                    trace_run_id=trace_id,
                    search_duration_ms=rag_result.duration_ms,
                    total_duration_ms=int((time.time() - t_total_start) * 1000),
                    recall_count=0,
                    final_count=0,
                    model_name=rag_result.model_name or "",
                    status="guidance",
                    metadata={
                        "queryUnderstanding": rag_result.intent,
                        "neighborExpansion": neighbor_expansion,
                    },
                )
                await db.flush()
                yield (
                    "event: finish\ndata: "
                    + json.dumps(
                        {
                            "messageId": assistant_msg_id,
                            "sources": [],
                            "recommendedQuestions": [],
                            "modes": {
                                "agenticRag": effective_agentic_rag,
                                "graphRag": effective_graph_rag,
                                "neighborExpansion": neighbor_expansion,
                            },
                            "channels": rag_result.channel_statuses,
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                yield "event: done\ndata: {}\n\n"
                return

            if not rag_result.answerable:
                refusal = "当前授权范围内没有找到足够可靠的资料，暂时无法回答该问题。"
                yield (
                    "event: message\ndata: "
                    + json.dumps({"type": "response", "delta": refusal})
                    + "\n\n"
                )
                assistant_msg_id = await chat_service.save_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=refusal,
                    sources=[],
                    agent_steps=agent_steps,
                    rag_modes={
                        "agenticRag": effective_agentic_rag,
                        "graphRag": effective_graph_rag,
                    },
                    retrieval_channels=rag_result.channel_statuses,
                )
                await trace.finalize(
                    trace_run_id=trace_id,
                    search_duration_ms=rag_result.duration_ms,
                    total_duration_ms=int((time.time() - t_total_start) * 1000),
                    recall_count=0,
                    final_count=0,
                    model_name=rag_result.model_name or "",
                    status="rejected",
                    rejection_reason=rag_result.rejection_reason,
                    metadata={
                        "channels": rag_result.channel_statuses,
                        "agenticRag": effective_agentic_rag,
                        "graphRag": effective_graph_rag,
                        "neighborExpansion": neighbor_expansion,
                    },
                )
                await db.flush()
                finish = json.dumps(
                    {
                        "messageId": assistant_msg_id,
                        "sources": [],
                        "modes": {
                            "agenticRag": effective_agentic_rag,
                            "graphRag": effective_graph_rag,
                            "neighborExpansion": neighbor_expansion,
                        },
                        "channels": rag_result.channel_statuses,
                    }
                )
                yield f"event: finish\ndata: {finish}\n\n"
                yield "event: done\ndata: {}\n\n"
                return

            # 6. Build prompt with context
            if settings.ttft_early_feedback:
                yield (
                    "event: progress\ndata: "
                    + json.dumps({"stage": "generating", "message": "正在生成回答..."}, ensure_ascii=False)
                    + "\n\n"
                )
            context_text = "\n\n---\n\n".join(
                f"[来源 {i + 1}] {c['content']}" for i, c in enumerate(rag_result.context_chunks)
            )
            if rag_result.response_mode == "system":
                system_prompt = "你是一个简洁、友好的企业智能助手。直接回应用户，不要声称检索了资料。"
            else:
                custom_prompt = (rag_result.prompt_template or "").strip()
                if custom_prompt:
                    rendered_prompt = custom_prompt
                    rendered_prompt = rendered_prompt.replace("{{context}}", context_text)
                    rendered_prompt = rendered_prompt.replace("{context}", context_text)
                    rendered_prompt = rendered_prompt.replace("{{question}}", question)
                    rendered_prompt = rendered_prompt.replace("{question}", question)
                    if "{{context}}" not in custom_prompt and "{context}" not in custom_prompt:
                        rendered_prompt += f"\n\n参考资料：\n{context_text}"
                    system_prompt = (
                        f"{rendered_prompt}\n\n"
                        "请仅依据参考资料作答，关键事实用 [1]、[2] 标注来源；"
                        "资料不足时明确说明。"
                    )
                else:
                    system_prompt = (
                        "你是一个智能助手，请严格根据以下参考资料回答用户问题。"
                        "答案中的关键事实使用 [1]、[2] 形式标注对应来源；"
                        "如果参考资料不足，请明确说明。\n\n"
                        f"参考资料：\n{context_text}"
                    )
            messages = [{"role": "system", "content": system_prompt}]
            for h in history[-16:]:
                messages.append({"role": h["role"], "content": h["content"]})
            messages.append({"role": "user", "content": question})

            # 7. Select LLM client (model router)
            t_llm_start = time.time()
            llm_client = get_llm_client(
                api_key=rag_result.model_api_key,
                base_url=rag_result.model_base_url,
                model=rag_result.model_name,
            )

            # 8. Stream LLM response
            full_content = ""
            thinking_content = ""

            async for token in llm_client.chat_stream(messages):
                if await request.is_disconnected():
                    break
                if token.startswith("__THINK__"):
                    thinking_content += token[9:]
                    yield f"event: message\ndata: {json.dumps({'type': 'think', 'delta': token[9:]})}\n\n"
                else:
                    full_content += token
                    yield f"event: message\ndata: {json.dumps({'type': 'response', 'delta': token})}\n\n"

            llm_duration = int((time.time() - t_llm_start) * 1000)

            # 9. Save assistant message
            recommended_questions = await recommend_questions(
                db,
                question=question,
                kb_id=resolved_kb_id,
                tenant_id=user.tenant_id or "default",
                sources=rag_result.sources,
            )
            assistant_msg_id = await chat_service.save_message(
                conversation_id=conversation_id,
                role="assistant", content=full_content,
                thinking_content=thinking_content or None,
                sources=rag_result.sources,
                recommended_questions=recommended_questions,
                agent_steps=agent_steps,
                rag_modes={
                    "agenticRag": effective_agentic_rag,
                    "graphRag": effective_graph_rag,
                },
                retrieval_channels=rag_result.channel_statuses,
            )
            await db.flush()
            await chat_service.maybe_summarize(conversation_id)

            # 10. Finalize trace
            total_duration = int((time.time() - t_total_start) * 1000)
            await trace.finalize(
                trace_run_id=trace_id,
                search_duration_ms=rag_result.duration_ms,
                llm_duration_ms=llm_duration,
                total_duration_ms=total_duration,
                recall_count=len(rag_result.context_chunks),
                final_count=len(rag_result.sources),
                model_name=rag_result.model_name or "",
                metadata={
                    "channels": rag_result.channel_statuses,
                    "agenticRag": effective_agentic_rag,
                    "graphRag": effective_graph_rag,
                    "neighborExpansion": neighbor_expansion,
                },
            )

            # 11. Send finish
            finish = json.dumps(
                {
                    "messageId": assistant_msg_id,
                    "sources": rag_result.sources,
                    "recommendedQuestions": recommended_questions,
                    "modes": {
                        "agenticRag": effective_agentic_rag,
                        "graphRag": effective_graph_rag,
                        "neighborExpansion": neighbor_expansion,
                    },
                    "channels": rag_result.channel_statuses,
                }
            )
            yield f"event: finish\ndata: {finish}\n\n"
            yield "event: done\ndata: {}\n\n"

        except Exception as e:
            error_msg = str(e)
            if trace_id:
                try:
                    await trace.finalize(trace_id, status="error", error_message=error_msg)
                except Exception:
                    pass
            yield f"event: error\ndata: {json.dumps({'error': error_msg})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
