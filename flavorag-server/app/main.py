from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
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
from app.api.security import router as security_router
from app.api.evaluation import router as evaluation_router
from app.api.graph import router as graph_router
from app.api.monitoring import router as monitoring_router
from app.api.assets import router as assets_router
from app.api.user_profile import router as user_profile_router
from app.config.settings import settings
from app.config.logging_config import get_logger, configure_root_logger
from app.observability.metrics import MetricsMiddleware, render_metrics
from app.observability.otel import setup_otel

_log = get_logger("flavorag.server")
_url_scheduler = None
_doc_schedule_scheduler = None
_index_sync_scheduler = None
_ingestion_watchdog = None
_ingestion_job_worker = None
_profile_scheduler = None
_index_repair_worker = None
_evaluation_job_worker = None
_retention_worker = None
_index_reconciliation_worker = None
_index_build_worker = None
_batch_import_worker = None


async def _seed_admin_user():
    """Create default admin user (admin/admin123) if no users exist."""
    from app.database.session import async_session_factory
    from app.models import Tenant, User
    from app.auth.jwt import hash_password
    from sqlalchemy import select, func

    try:
        async with async_session_factory() as session:
            tenant = (
                await session.execute(select(Tenant).where(Tenant.id == "default"))
            ).scalar_one_or_none()
            if tenant is None:
                session.add(Tenant(id="default", name="Default Tenant", enabled=1))
                # Commit the tenant eagerly: it must exist even when the
                # admin seed below is skipped (users already present), because
                # User.tenant_id defaults to "default" (FK to t_tenant).
                await session.commit()
            result = await session.execute(select(func.count(User.id)))
            user_count = result.scalar()
            if user_count == 0:
                admin = User(
                    username="admin",
                    password=hash_password("admin123"),
                    role="admin",
                    tenant_id="default",
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
    from app.database.session import engine

    _log.info("server_starting", database_backend=engine.url.get_backend_name())

    # Startup: create tables and apply additive compatibility upgrades for SQLite.
    # Production databases continue to use Alembic migrations.
    if settings.database_url.startswith("sqlite"):
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

    global _index_sync_scheduler
    try:
        from app.services.index_sync import IndexSyncRetryScheduler

        _index_sync_scheduler = IndexSyncRetryScheduler()
        await _index_sync_scheduler.start()
        _log.info("index_sync_retry_scheduler_started")
    except Exception as exc:
        _log.warning("index_sync_retry_scheduler_failed", error=str(exc))

    global _index_repair_worker
    try:
        from app.services.index_repair import IndexRepairWorker

        _index_repair_worker = IndexRepairWorker()
        await _index_repair_worker.start()
        _log.info("index_repair_worker_started")
    except Exception as exc:
        _log.warning("index_repair_worker_failed", error=str(exc))

    global _ingestion_watchdog
    try:
        from app.services.ingestion_watchdog import IngestionWatchdog

        _ingestion_watchdog = IngestionWatchdog()
        await _ingestion_watchdog.start()
        _log.info("ingestion_watchdog_started")
    except Exception as exc:
        _log.warning("ingestion_watchdog_failed", error=str(exc))

    global _evaluation_job_worker
    try:
        from app.services.evaluation_jobs import EvaluationJobWorker

        _evaluation_job_worker = EvaluationJobWorker()
        await _evaluation_job_worker.start()
        _log.info("evaluation_job_worker_started")
    except Exception as exc:
        _log.warning("evaluation_job_worker_failed", error=str(exc))

    global _retention_worker
    try:
        from app.services.retention import RetentionWorker

        _retention_worker = RetentionWorker()
        await _retention_worker.start()
        _log.info("retention_worker_started")
    except Exception as exc:
        _log.warning("retention_worker_failed", error=str(exc))

    global _index_reconciliation_worker
    try:
        from app.services.index_reconciliation import (
            IndexReconciliationWorker,
        )

        _index_reconciliation_worker = IndexReconciliationWorker()
        await _index_reconciliation_worker.start()
        _log.info("index_reconciliation_worker_started")
    except Exception as exc:
        _log.warning("index_reconciliation_worker_failed", error=str(exc))

    global _index_build_worker
    try:
        from app.services.index_lifecycle import IndexBuildWorker

        _index_build_worker = IndexBuildWorker()
        await _index_build_worker.start()
        _log.info("index_build_worker_started")
    except Exception as exc:
        _log.warning("index_build_worker_failed", error=str(exc))

    global _batch_import_worker
    try:
        from app.services.batch_import import BatchImportWorker

        _batch_import_worker = BatchImportWorker()
        await _batch_import_worker.start()
        _log.info("batch_import_worker_started")
    except Exception as exc:
        _log.warning("batch_import_worker_failed", error=str(exc))

    # Start async ingestion outbox worker
    global _ingestion_job_worker
    if settings.ingestion_async_enabled:
        try:
            from app.services.ingestion_jobs import IngestionJobWorker

            _ingestion_job_worker = IngestionJobWorker()
            await _ingestion_job_worker.start()
            _log.info("ingestion_job_worker_started")
        except Exception as exc:
            _log.warning("ingestion_job_worker_failed", error=str(exc))

    # Optional OpenTelemetry tracing (no-op unless otel_enabled)
    if setup_otel(app):
        _log.info("otel_enabled", endpoint=settings.otel_exporter_otlp_endpoint)

    # Start profile daily scheduler (mem0 user profiling)
    global _profile_scheduler
    try:
        from app.memory.profile_scheduler import ProfileDailyScheduler

        _profile_scheduler = ProfileDailyScheduler()
        await _profile_scheduler.start()
        _log.info("profile_scheduler_started")
    except Exception as exc:
        _log.warning("profile_scheduler_failed", error=str(exc))

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
    if _index_sync_scheduler:
        try:
            await _index_sync_scheduler.stop()
            _log.info("index_sync_retry_scheduler_stopped")
        except Exception as exc:
            _log.warning("index_sync_retry_scheduler_stop_failed", error=str(exc))
    if _index_repair_worker:
        try:
            await _index_repair_worker.stop()
            _log.info("index_repair_worker_stopped")
        except Exception as exc:
            _log.warning("index_repair_worker_stop_failed", error=str(exc))
    if _ingestion_watchdog:
        try:
            await _ingestion_watchdog.stop()
            _log.info("ingestion_watchdog_stopped")
        except Exception as exc:
            _log.warning("ingestion_watchdog_stop_failed", error=str(exc))
    if _evaluation_job_worker:
        try:
            await _evaluation_job_worker.stop()
            _log.info("evaluation_job_worker_stopped")
        except Exception as exc:
            _log.warning("evaluation_job_worker_stop_failed", error=str(exc))
    if _retention_worker:
        try:
            await _retention_worker.stop()
            _log.info("retention_worker_stopped")
        except Exception as exc:
            _log.warning("retention_worker_stop_failed", error=str(exc))
    if _index_reconciliation_worker:
        try:
            await _index_reconciliation_worker.stop()
            _log.info("index_reconciliation_worker_stopped")
        except Exception as exc:
            _log.warning(
                "index_reconciliation_worker_stop_failed", error=str(exc)
            )
    if _index_build_worker:
        try:
            await _index_build_worker.stop()
            _log.info("index_build_worker_stopped")
        except Exception as exc:
            _log.warning("index_build_worker_stop_failed", error=str(exc))
    if _batch_import_worker:
        try:
            await _batch_import_worker.stop()
            _log.info("batch_import_worker_stopped")
        except Exception as exc:
            _log.warning("batch_import_worker_stop_failed", error=str(exc))
    if _ingestion_job_worker:
        try:
            await _ingestion_job_worker.stop()
            _log.info("ingestion_job_worker_stopped")
        except Exception as exc:
            _log.warning("ingestion_job_worker_stop_failed", error=str(exc))
    if _profile_scheduler:
        try:
            await _profile_scheduler.stop()
            _log.info("profile_scheduler_stopped")
        except Exception as exc:
            _log.warning("profile_scheduler_stop_failed", error=str(exc))
    try:
        from app.rag.graph.neo4j_store import close_neo4j_driver

        await close_neo4j_driver()
    except Exception as exc:
        _log.warning("neo4j_driver_close_failed", error=str(exc))
    try:
        from app.observability.otel import shutdown_otel

        shutdown_otel()
    except Exception as exc:
        _log.warning("otel_shutdown_failed", error=str(exc))
    # Close shared ES client
    try:
        from app.rag.search.keyword import close_es_client

        await close_es_client()
        _log.info("es_client_closed")
    except Exception as exc:
        _log.warning("es_client_close_failed", error=str(exc))
    # Dispose DB engine: aiosqlite pooled connections each hold a non-daemon
    # worker thread — without dispose() the process never exits and uvicorn
    # reload hangs forever waiting to join the old worker.
    try:
        await engine.dispose()
        _log.info("db_engine_disposed")
    except Exception as exc:
        _log.warning("db_engine_dispose_failed", error=str(exc))
    _log.info("server_shutting_down")


app = FastAPI(title="flavor-rag API", version="0.0.5", lifespan=lifespan)

app.add_middleware(AuditMiddleware)
if settings.metrics_enabled:
    app.add_middleware(MetricsMiddleware)
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
app.include_router(security_router)
app.include_router(evaluation_router)
app.include_router(graph_router)
app.include_router(monitoring_router)
app.include_router(assets_router)
app.include_router(user_profile_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "0.0.5"}


@app.get("/api/health/live")
async def liveness_check():
    return {"status": "ok", "version": "0.0.5"}


@app.get("/api/health/ready")
async def readiness_check():
    from app.health import is_ready, readiness_checks

    checks = await readiness_checks()
    ready = is_ready(checks)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "version": "0.0.5",
            "checks": checks,
        },
    )


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    if not settings.metrics_enabled:
        return Response(status_code=404)
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
