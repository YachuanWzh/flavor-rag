"""Document scheduled refresh — Cron-based document re-indexing.

Provides:
  - ScheduleLockManager: distributed lock via PostgreSQL advisory lock
  - RefreshProcessor: detect changes + re-ingest documents
  - DocumentScheduleScheduler: periodic poll loop integrated with FastAPI lifespan
"""
