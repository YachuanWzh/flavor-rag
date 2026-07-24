"""Chat service — message persistence, history retrieval."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.models import Message, gen_id


class ChatService:
    """Handles message persistence and conversation history."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_recent_messages(
        self, conversation_id: str, turns: int = 8
    ) -> list[dict]:
        """Get recent N turns of conversation history."""
        result = await self.db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.deleted == 0,
            )
            .order_by(desc(Message.create_time))
            .limit(turns * 2)  # N turns = 2N messages (user + assistant)
        )
        messages = list(result.scalars().all())
        # Return in chronological order
        messages.reverse()
        return [
            {"role": m.role, "content": m.content}
            for m in messages[-turns * 2 :]
        ]

    async def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        thinking_content: str | None = None,
        sources: list[dict] | None = None,
    ) -> str:
        """Save a message and return its ID."""
        msg = Message(
            id=gen_id(),
            conversation_id=conversation_id or "",
            user_id="system",  # Will be overridden by caller
            role=role,
            content=content,
            thinking_content=thinking_content,
            sources=sources,
            create_time=datetime.utcnow(),
        )
        self.db.add(msg)
        await self.db.flush()
        return msg.id
