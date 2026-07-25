from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router
from app.api.knowledge import router as knowledge_router
from app.api.search import router as search_router
from app.api.conversation import router as conversation_router
from app.api.chat import router as chat_router
from app.api.admin import router as admin_router
from app.config.settings import settings
from app.config.logging_config import get_logger, configure_root_logger

_log = get_logger("flavorag.server")


async def _seed_admin_user():
    """Create default admin user (admin/admin123) if no users exist."""
    from app.database.session import async_session_factory
    from app.models import User
    from app.auth.jwt import hash_password
    from sqlalchemy import select, func

    try:
        async with async_session_factory() as session:
            result = await session.execute(select(func.count(User.id)))
            user_count = result.scalar()
            if user_count == 0:
                admin = User(
                    username="admin",
                    password=hash_password("admin123"),
                    role="admin",
                )
                session.add(admin)
                await session.commit()
                _log.info("seed_admin_created", username="admin", role="admin")
            else:
                _log.info("seed_admin_skipped", existing_users=user_count)
    except Exception as exc:
        _log.error("seed_admin_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_root_logger()
    _log.info("server_starting", database_url=settings.database_url[:30] + "...")

    # Startup: auto-create tables when using SQLite
    if settings.database_url.startswith("sqlite"):
        from app.models import Base
        from app.database.session import engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _log.info("sqlite_tables_created")

    # Seed admin user for all backends
    await _seed_admin_user()
    _log.info("server_started", port=settings.server_port)
    yield
    _log.info("server_shutting_down")


app = FastAPI(title="flavor-rag API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(knowledge_router)
app.include_router(search_router)
app.include_router(conversation_router)
app.include_router(chat_router)
app.include_router(admin_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
