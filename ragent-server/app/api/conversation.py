"""Conversation CRUD API — create/list/delete/rename sessions."""
from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, Conversation, Message, gen_id

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
        last_time=datetime.utcnow(),
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
        "data": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "thinkingContent": m.thinking_content,
                "sources": m.sources,
                "createTime": str(m.create_time),
            }
            for m in messages
        ],
    }
