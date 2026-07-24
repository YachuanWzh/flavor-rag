"""SSE streaming chat API — GET /api/rag/v3/chat."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, Conversation, gen_id
from app.rag.pipeline import RAGPipeline, RAGContext
from app.llm.client import get_llm_client
from app.services.chat_service import ChatService
from datetime import datetime

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
    """SSE streaming RAG chat endpoint."""

    rag_pipeline = RAGPipeline()
    llm_client = get_llm_client()

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
            # 1. Get history
            history = await chat_service.get_recent_messages(conversation_id, turns=8)

            # 2. Save user message
            await chat_service.save_message(
                conversation_id=conversation_id,
                role="user",
                content=question,
            )

            # 3. RAG retrieval
            ctx = RAGContext(
                question=question,
                conversation_id=conversation_id,
                kb_id=kb_id,
                history=history,
                deep_thinking=deep_thinking,
            )
            rag_result = await rag_pipeline.run(ctx)

            # 4. Send meta
            meta = json.dumps({
                "conversationId": conversation_id,
                "taskId": rag_result.question[:20],
            })
            yield f"event: meta\ndata: {meta}\n\n"

            # 5. Build prompt
            context_text = "\n\n---\n\n".join(
                f"[来源 {i + 1}] {c['content']}"
                for i, c in enumerate(rag_result.context_chunks)
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

            # 6. Stream LLM response
            full_content = ""
            thinking_content = ""

            async for token in llm_client.chat_stream(messages):
                if await request.is_disconnected():
                    break

                if token.startswith("__THINK__"):
                    think_text = token[9:]
                    thinking_content += think_text
                    yield f"event: message\ndata: {json.dumps({'type': 'think', 'delta': think_text})}\n\n"
                else:
                    full_content += token
                    yield f"event: message\ndata: {json.dumps({'type': 'response', 'delta': token})}\n\n"

            # 7. Save assistant message
            assistant_msg_id = await chat_service.save_message(
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                thinking_content=thinking_content or None,
                sources=rag_result.sources,
            )

            # 8. Send finish event
            finish = json.dumps({
                "messageId": assistant_msg_id,
                "sources": rag_result.sources,
            })
            yield f"event: finish\ndata: {finish}\n\n"
            yield "event: done\ndata: {}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
