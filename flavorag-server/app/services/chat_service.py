"""Chat service — message persistence, history retrieval."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from app.models import Conversation, Message, gen_id
from app.config.settings import settings


class ChatService:
    """Handles message persistence and conversation history."""

    def __init__(self, db: AsyncSession, *, user_id: str = "system"):
        self.db = db
        self.user_id = user_id

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

    async def get_context(self, conversation_id: str, turns: int = 8) -> list[dict]:
        conversation = (
            await self.db.execute(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == self.user_id,
                    Conversation.deleted == 0,
                )
            )
        ).scalar_one_or_none()
        history = await self.get_recent_messages(conversation_id, turns=turns)
        if conversation and conversation.summary:
            return [
                {
                    "role": "system",
                    "content": f"Earlier conversation summary: {conversation.summary}",
                },
                *history,
            ]
        return history

    async def maybe_summarize(self, conversation_id: str) -> str | None:
        """Create an extractive bounded summary of older turns.

        This deterministic baseline avoids making chat persistence depend on an
        external model. Deployments can replace it with an LLM summarizer while
        retaining the same storage and trigger contract.
        """
        trigger = settings.conversation_summary_trigger_messages
        keep = settings.conversation_summary_keep_recent_messages
        messages = list(
            (
                await self.db.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.deleted == 0,
                    )
                    .order_by(Message.create_time)
                )
            ).scalars().all()
        )
        if len(messages) <= trigger or len(messages) <= keep:
            return None
        older = messages[:-keep]
        summary = "\n".join(
            f"{message.role}: {' '.join(message.content.split())[:320]}"
            for message in older
        )[-4000:]
        conversation = (
            await self.db.execute(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == self.user_id,
                    Conversation.deleted == 0,
                )
            )
        ).scalar_one_or_none()
        if conversation:
            conversation.summary = summary
            conversation.summary_message_count = len(older)
        return summary

    async def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        thinking_content: str | None = None,
        sources: list[dict] | None = None,
        agent_steps: list[dict] | None = None,
        rag_modes: dict | None = None,
        retrieval_channels: dict | None = None,
    ) -> str:
        """Save a message and return its ID."""
        msg = Message(
            id=gen_id(),
            conversation_id=conversation_id or "",
            user_id=self.user_id,
            role=role,
            content=content,
            thinking_content=thinking_content,
            sources=sources,
            agent_steps=agent_steps,
            rag_modes=rag_modes,
            retrieval_channels=retrieval_channels,
            create_time=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.db.add(msg)
        await self.db.flush()
        return msg.id
