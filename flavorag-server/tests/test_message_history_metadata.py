from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import pytest


@pytest.mark.asyncio
async def test_save_message_persists_rag_badge_metadata(tmp_path):
    from app.database.sqlite_schema import initialize_sqlite_schema
    from app.models import Message
    from app.services.chat_service import ChatService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    agent_steps = [{"step": 1, "tool": "retrieve", "status": "success"}]
    rag_modes = {"agenticRag": True, "graphRag": True}
    channels = {
        "graph": {
            "status": "success",
            "duration_ms": 12,
            "count": 3,
            "error": None,
        }
    }
    try:
        await initialize_sqlite_schema(engine)
        async with sessions() as session:
            message_id = await ChatService(session, user_id="user-1").save_message(
                conversation_id="conversation-1",
                role="assistant",
                content="answer",
                agent_steps=agent_steps,
                rag_modes=rag_modes,
                retrieval_channels=channels,
            )
            await session.commit()
            message = await session.get(Message, message_id)

        assert message is not None
        assert message.agent_steps == agent_steps
        assert message.rag_modes == rag_modes
        assert message.retrieval_channels == channels
    finally:
        await engine.dispose()


def test_message_payload_restores_rag_badge_metadata():
    from app.api.conversation import _message_payload
    from app.models import Message

    message = Message(
        id="message-1",
        conversation_id="conversation-1",
        user_id="user-1",
        role="assistant",
        content="answer",
        agent_steps=[{"step": 1, "tool": "retrieve", "status": "success"}],
        rag_modes={"agenticRag": True, "graphRag": True},
        retrieval_channels={"graph": {"status": "success", "count": 2}},
    )

    payload = _message_payload(message)

    assert payload["agentSteps"] == message.agent_steps
    assert payload["ragModes"] == message.rag_modes
    assert payload["retrievalChannels"] == message.retrieval_channels
