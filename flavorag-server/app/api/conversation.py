"""Conversation CRUD API — create/list/delete/rename sessions + message feedback."""
from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel, Field

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, Conversation, Message, MessageFeedback, gen_id

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationResponse(BaseModel):
    id: str
    conversationId: str
    title: str
    lastTime: str | None = None


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    thinkingContent: str | None = None
    sources: list | None = None
    recommendedQuestions: list | None = None
    agentSteps: list | None = None
    ragModes: dict | None = None
    retrievalChannels: dict | None = None
    createTime: str


# ---- Conversation CRUD ----


@router.get("")
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.deleted == 0)
        .order_by(desc(Conversation.last_time))
    )
    convs = result.scalars().all()
    return {
        "code": "0",
        "message": "success",
        "data": [
            {
                "id": c.id,
                "conversationId": c.conversation_id,
                "title": c.title,
                "lastTime": str(c.last_time) if c.last_time else None,
            }
            for c in convs
        ],
    }


@router.post("")
async def create_conversation(
    title: str = "新对话",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv_id = gen_id()
    conv = Conversation(
        id=conv_id,
        conversation_id=conv_id,
        user_id=user.id,
        title=title,
        last_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(conv)
    await db.flush()

    return {
        "code": "0",
        "message": "success",
        "data": {"id": conv.id, "conversationId": conv.conversation_id, "title": conv.title},
    }


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.user_id == user.id,
            Conversation.deleted == 0,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    conv.deleted = 1
    return {"code": "0", "message": "success", "data": None}


@router.put("/{conv_id}")
async def rename_conversation(
    conv_id: str,
    title: str = "新对话",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.user_id == user.id,
            Conversation.deleted == 0,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    conv.title = title
    return {"code": "0", "message": "success", "data": None}


# ---- Messages ----


def _message_payload(message: Message) -> dict:
    """Serialize persisted message metadata using the frontend API contract."""
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "thinkingContent": message.thinking_content,
        "sources": message.sources,
        "recommendedQuestions": message.recommended_questions,
        "agentSteps": message.agent_steps,
        "ragModes": message.rag_modes,
        "retrievalChannels": message.retrieval_channels,
        "hydeDoc": message.hyde_doc,
        "hydeMeta": message.hyde_meta,
        "createTime": str(message.create_time),
    }


@router.get("/{conv_id}/messages")
async def list_messages(
    conv_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Verify conversation ownership
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.user_id == user.id,
            Conversation.deleted == 0,
        )
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="会话不存在")

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id, Message.deleted == 0)
        .order_by(Message.create_time)
    )
    messages = result.scalars().all()
    return {
        "code": "0",
        "message": "success",
        "data": [_message_payload(message) for message in messages],
    }


# ---- Message Feedback ----


class FeedbackRequest(BaseModel):
    message_id: str = Field(..., description="消息ID")
    vote: int = Field(..., ge=-1, le=1, description="1=赞, -1=踩")
    reason: str | None = Field(None, max_length=255, description="反馈原因")
    comment: str | None = Field(None, max_length=1024, description="补充评论")


@router.post("/feedback")
async def submit_feedback(
    req: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit thumbs-up/thumbs-down feedback for a message."""
    # Verify message exists
    msg_result = await db.execute(
        select(Message).where(
            Message.id == req.message_id,
            Message.deleted == 0,
        )
    )
    message = msg_result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="消息不存在")

    # Upsert: check if user already voted on this message
    existing = await db.execute(
        select(MessageFeedback).where(
            MessageFeedback.message_id == req.message_id,
            MessageFeedback.user_id == user.id,
            MessageFeedback.deleted == 0,
        )
    )
    feedback = existing.scalar_one_or_none()

    if feedback:
        # Update existing
        feedback.vote = req.vote
        feedback.reason = req.reason
        feedback.comment = req.comment
        feedback.update_time = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        # Create new
        feedback = MessageFeedback(
            id=gen_id(),
            message_id=req.message_id,
            conversation_id=message.conversation_id,
            user_id=user.id,
            vote=req.vote,
            reason=req.reason,
            comment=req.comment,
        )
        db.add(feedback)

    await db.flush()
    return {"code": "0", "message": "success", "data": {"id": feedback.id, "vote": feedback.vote}}


@router.get("/feedback/{message_id}")
async def get_feedback(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the current user's feedback for a specific message."""
    result = await db.execute(
        select(MessageFeedback).where(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == user.id,
            MessageFeedback.deleted == 0,
        )
    )
    fb = result.scalar_one_or_none()
    if not fb:
        return {"code": "0", "message": "success", "data": None}
    return {
        "code": "0",
        "message": "success",
        "data": {
            "id": fb.id,
            "vote": fb.vote,
            "reason": fb.reason,
            "comment": fb.comment,
        },
    }
