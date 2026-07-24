"""SSE streaming chat API — GET /api/rag/v3/chat with trace + rate limit."""
from __future__ import annotations

import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, Conversation, gen_id
from app.rag.pipeline import RAGPipeline, RAGContext
from app.rag.trace import TraceLogger
from app.rag.rate_limiter import RateLimiter
from app.llm.client import get_llm_client
from app.services.chat_service import ChatService
from app.config.settings import settings

router = APIRouter(prefix="/api/rag/v3", tags=["chat"])


@router.get("/chat")
async def chat(
    request: Request,
    question: str = Query(..., description="用户问题"),
    conversation_id: str | None = Query(None, description="会话ID"),
    kb_id: str | None = Query(None, description="知识库ID"),
    deep_thinking: bool = Query(False, description="启用深度思考"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """SSE streaming RAG chat endpoint with trace + rate limiting."""
    t_total_start = time.time()

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
    )

    rag_pipeline = RAGPipeline(trace_logger=trace)

    # Auto-create conversation if not provided
    if not conversation_id:
        conv = Conversation(
            id=gen_id(),
            conversation_id=gen_id(),
            user_id=user.id,
            title=question[:30] + ("..." if len(question) > 30 else ""),
            last_time=datetime.utcnow(),
        )
        db.add(conv)
        await db.flush()
        conversation_id = conv.id

    chat_service = ChatService(db)

    async def event_stream():
        try:
            # 1. History
            history = await chat_service.get_recent_messages(conversation_id, turns=8)

            # 2. Save user message
            await chat_service.save_message(conversation_id=conversation_id, role="user", content=question)
            await db.flush()

            # 3. RAG retrieval
            ctx = RAGContext(
                question=question, conversation_id=conversation_id,
                kb_id=kb_id, history=history, deep_thinking=deep_thinking,
            )
            rag_result = await rag_pipeline.run(ctx)

            # 4. Send meta
            meta = json.dumps({"conversationId": conversation_id, "taskId": trace_id})
            yield f"event: meta\ndata: {meta}\n\n"

            # 5. Build prompt with context
            context_text = "\n\n---\n\n".join(
                f"[来源 {i + 1}] {c['content']}" for i, c in enumerate(rag_result.context_chunks)
            )
            system_prompt = (
                "你是一个智能助手，请根据以下参考资料回答用户问题。"
                "如果参考资料不足以回答问题，请如实告知。\n\n"
                f"参考资料：\n{context_text}"
            )
            messages = [{"role": "system", "content": system_prompt}]
            for h in history[-16:]:
                messages.append({"role": h["role"], "content": h["content"]})
            messages.append({"role": "user", "content": question})

            # 6. Select LLM client (model router)
            t_llm_start = time.time()
            llm_client = get_llm_client(
                api_key=rag_result.model_api_key,
                base_url=rag_result.model_base_url,
                model=rag_result.model_name,
            )

            # 7. Stream LLM response
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

            # 8. Save assistant message
            assistant_msg_id = await chat_service.save_message(
                conversation_id=conversation_id,
                role="assistant", content=full_content,
                thinking_content=thinking_content or None,
                sources=rag_result.sources,
            )
            await db.flush()

            # 9. Finalize trace
            total_duration = int((time.time() - t_total_start) * 1000)
            await trace.finalize(
                trace_run_id=trace_id,
                search_duration_ms=rag_result.duration_ms,
                llm_duration_ms=llm_duration,
                total_duration_ms=total_duration,
                recall_count=len(rag_result.context_chunks),
                final_count=len(rag_result.sources),
                model_name=rag_result.model_name or "",
            )

            # 10. Send finish
            finish = json.dumps({"messageId": assistant_msg_id, "sources": rag_result.sources})
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
