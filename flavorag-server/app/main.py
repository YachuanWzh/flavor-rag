from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router
from app.api.knowledge import router as knowledge_router
from app.api.search import router as search_router
from app.api.conversation import router as conversation_router
from app.api.chat import router as chat_router
from app.api.admin import router as admin_router
from app.api.intent_tree import router as intent_tree_router
from app.api.sample_question import router as sample_question_router
from app.api.query_term_mapping import router as query_term_mapping_router
from app.audit.api import router as audit_router
from app.audit.middleware import AuditMiddleware
from app.api.schedule import router as schedule_router
from app.api.ingestion_pipeline import router as ingestion_pipeline_router
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

    # Startup: create tables and apply additive compatibility upgrades for SQLite.
    # Production databases continue to use Alembic migrations.
    if settings.database_url.startswith("sqlite"):
        from app.database.session import engine
        from app.database.sqlite_schema import initialize_sqlite_schema

        added_columns = await initialize_sqlite_schema(engine)
        _log.info(
            "sqlite_schema_initialized",
            added_columns=added_columns,
        )

    # Seed admin user for all backends
    await _seed_admin_user()

    # Start URL refresh scheduler
    global _url_scheduler
    try:
        from app.services.url_refresh_scheduler import URLRefreshScheduler
        _url_scheduler = URLRefreshScheduler()
        await _url_scheduler.start()
        _log.info("url_scheduler_started")
    except Exception as exc:
        _log.warning("url_scheduler_failed", error=str(exc))

    # Start document schedule scheduler
    global _doc_schedule_scheduler
    try:
        from app.services.schedule.scheduler import DocumentScheduleScheduler
        _doc_schedule_scheduler = DocumentScheduleScheduler(poll_interval_sec=60)
        await _doc_schedule_scheduler.start()
        _log.info("doc_schedule_scheduler_started")
    except Exception as exc:
        _log.warning("doc_schedule_scheduler_failed", error=str(exc))

    _log.info("server_started", port=settings.server_port)
    yield
    # Shutdown: stop schedulers
    if _url_scheduler:
        try:
            await _url_scheduler.stop()
            _log.info("url_scheduler_stopped")
        except Exception as exc:
            _log.warning("url_scheduler_stop_failed", error=str(exc))
    if _doc_schedule_scheduler:
        try:
            await _doc_schedule_scheduler.stop()
            _log.info("doc_schedule_scheduler_stopped")
        except Exception as exc:
            _log.warning("doc_schedule_scheduler_stop_failed", error=str(exc))
    _log.info("server_shutting_down")


app = FastAPI(title="flavor-rag API", version="0.2.0", lifespan=lifespan)

app.add_middleware(AuditMiddleware)
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
app.include_router(intent_tree_router)
app.include_router(sample_question_router)
app.include_router(query_term_mapping_router)
app.include_router(audit_router)
app.include_router(schedule_router)
app.include_router(ingestion_pipeline_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
