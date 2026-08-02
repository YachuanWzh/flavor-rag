"""Worker registry — declares all background workers and resolution logic."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerSpec:
    module_path: str
    class_name: str
    description: str


WORKER_REGISTRY: dict[str, WorkerSpec] = {
    "ingestion": WorkerSpec(
        module_path="app.services.ingestion_jobs",
        class_name="IngestionJobWorker",
        description="Polls ingestion outbox jobs and executes document processing",
    ),
    "evaluation": WorkerSpec(
        module_path="app.services.evaluation_jobs",
        class_name="EvaluationJobWorker",
        description="Runs offline retrieval/generation evaluation jobs",
    ),
    "retention": WorkerSpec(
        module_path="app.services.retention",
        class_name="RetentionWorker",
        description="Purges expired traces and retired index generations",
    ),
    "reconciliation": WorkerSpec(
        module_path="app.services.index_reconciliation",
        class_name="IndexReconciliationWorker",
        description="Compares PostgreSQL chunks with external indexes and queues repairs",
    ),
    "repair": WorkerSpec(
        module_path="app.services.index_repair",
        class_name="IndexRepairWorker",
        description="Executes queued index repair operations",
    ),
    "index_sync": WorkerSpec(
        module_path="app.services.index_sync",
        class_name="IndexSyncRetryScheduler",
        description="Retries failed index sync jobs",
    ),
    "watchdog": WorkerSpec(
        module_path="app.services.ingestion_watchdog",
        class_name="IngestionWatchdog",
        description="Detects stalled ingestion tasks and triggers recovery",
    ),
    "batch_import": WorkerSpec(
        module_path="app.services.batch_import",
        class_name="BatchImportWorker",
        description="Processes batch file import jobs",
    ),
    "url_refresh": WorkerSpec(
        module_path="app.services.url_refresh_scheduler",
        class_name="URLRefreshScheduler",
        description="Periodically refreshes URL-sourced documents",
    ),
    "doc_schedule": WorkerSpec(
        module_path="app.services.schedule.scheduler",
        class_name="DocumentScheduleScheduler",
        description="Executes cron-based document refresh schedules",
    ),
    "profile": WorkerSpec(
        module_path="app.memory.profile_scheduler",
        class_name="ProfileScheduler",
        description="Builds and updates user profile summaries",
    ),
    "index_build": WorkerSpec(
        module_path="app.services.index_lifecycle",
        class_name="IndexBuildWorker",
        description="Builds and promotes new index generations",
    ),
}


def resolve_workers(args: list[str]) -> list[str]:
    """Resolve CLI args to a list of worker names.

    Pass ``["--all"]`` to select every registered worker, or explicit names.
    Raises ValueError for unknown worker names.
    """
    if "--all" in args:
        return list(WORKER_REGISTRY.keys())
    resolved: list[str] = []
    for name in args:
        if name not in WORKER_REGISTRY:
            raise ValueError(f"unknown_worker: {name!r}")
        resolved.append(name)
    return resolved


def should_start_workers(mode: str) -> bool:
    """Return True when the current process should launch background workers."""
    return mode != "standalone"
