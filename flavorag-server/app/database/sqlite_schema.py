"""Development SQLite schema initialization and additive compatibility upgrades.

Production databases are versioned with Alembic. SQLite is also supported as a
zero-setup development database, where ``metadata.create_all`` creates missing
tables but cannot add columns to tables that already exist. Keep the small,
explicit compatibility upgrades here so an existing development database can
start safely after an additive model change.
"""

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import Base


_COMPATIBILITY_COLUMNS: dict[str, dict[str, str]] = {
    "t_user": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "department_id": "VARCHAR(64)",
    },
    "t_conversation": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "summary": "TEXT",
        "summary_message_count": "INTEGER DEFAULT 0",
    },
    "t_message": {
        "recommended_questions": "JSON",
        "agent_steps": "JSON",
        "rag_modes": "JSON",
        "retrieval_channels": "JSON",
    },
    "t_intent_node": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "kind": "VARCHAR(16) NOT NULL DEFAULT 'KB'",
        "score_threshold": "INTEGER DEFAULT 30",
        "examples": "JSON",
        "mcp_tool_id": "VARCHAR(128)",
    },
    "t_query_term_mapping": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
    },
    "t_sample_question": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
    },
    "t_knowledge_base": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "department_id": "VARCHAR(64)",
        "visibility": "VARCHAR(16) NOT NULL DEFAULT 'PRIVATE'",
    },
    "t_knowledge_document": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "department_id": "VARCHAR(64)",
        "visibility": "VARCHAR(16) NOT NULL DEFAULT 'INHERIT'",
        "content_hash": "VARCHAR(64)",
    },
    "t_knowledge_chunk": {
        "embedding_content": "TEXT",
        "block_type": "VARCHAR(32)",
        "page_start": "INTEGER",
        "page_end": "INTEGER",
        "bbox_json": "JSON",
        "metadata_json": "JSON",
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "department_id": "VARCHAR(64)",
    },
    "t_knowledge_asset": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "department_id": "VARCHAR(64)",
    },
    "t_rag_trace_run": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "kb_id": "VARCHAR(20)",
        "rejection_reason": "VARCHAR(64)",
        "metadata_json": "JSON",
    },
    "t_ingestion_pipeline": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "enabled": "SMALLINT NOT NULL DEFAULT 1",
    },
    "t_ingestion_task": {
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "kb_id": "VARCHAR(20)",
        "doc_id": "VARCHAR(20)",
        "trace_id": "VARCHAR(32)",
        "idempotency_key": "VARCHAR(128)",
        "parent_task_id": "VARCHAR(20)",
        "attempt": "INTEGER NOT NULL DEFAULT 1",
        "total_duration_ms": "BIGINT",
        "sla_ms": "BIGINT NOT NULL DEFAULT 300000",
        "heartbeat_at": "DATETIME",
    },
    "t_ingestion_task_node": {
        "attempt": "INTEGER NOT NULL DEFAULT 1",
        "started_at": "DATETIME",
        "completed_at": "DATETIME",
    },
}


async def initialize_sqlite_schema(engine: AsyncEngine) -> list[str]:
    """Create SQLite tables and add known missing columns.

    Returns the chunk column names added during this invocation. The SQL is
    deliberately static and the existing schema is inspected first, making
    repeated application idempotent.
    """
    if engine.url.get_backend_name() != "sqlite":
        raise ValueError("initialize_sqlite_schema requires a SQLite engine")

    added_columns: list[str] = []
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table_name, expected_columns in _COMPATIBILITY_COLUMNS.items():
            existing_columns = await conn.run_sync(
                lambda sync_conn, name=table_name: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns(name)
                }
            )
            for column_name, column_type in expected_columns.items():
                if column_name in existing_columns:
                    continue
                await conn.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                added_columns.append(f"{table_name}.{column_name}")
        index_specs = (
            (
                "t_knowledge_base",
                {"tenant_id", "deleted"},
                "CREATE INDEX IF NOT EXISTS idx_kb_tenant "
                "ON t_knowledge_base (tenant_id, deleted)",
            ),
            (
                "t_knowledge_document",
                {"tenant_id", "kb_id", "deleted"},
                "CREATE INDEX IF NOT EXISTS idx_document_tenant "
                "ON t_knowledge_document (tenant_id, kb_id, deleted)",
            ),
            (
                "t_knowledge_chunk",
                {"tenant_id", "kb_id", "doc_id", "deleted", "enabled"},
                "CREATE INDEX IF NOT EXISTS idx_chunk_tenant "
                "ON t_knowledge_chunk (tenant_id, kb_id, doc_id, deleted, enabled)",
            ),
            (
                "t_resource_acl",
                {"tenant_id", "resource_type", "resource_id"},
                "CREATE INDEX IF NOT EXISTS idx_acl_resource "
                "ON t_resource_acl (tenant_id, resource_type, resource_id)",
            ),
            (
                "t_resource_acl",
                {"tenant_id", "subject_type", "subject_id"},
                "CREATE INDEX IF NOT EXISTS idx_acl_subject "
                "ON t_resource_acl (tenant_id, subject_type, subject_id)",
            ),
            (
                "t_resource_acl",
                {
                    "tenant_id",
                    "subject_type",
                    "subject_id",
                    "resource_type",
                    "resource_id",
                    "deleted",
                },
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_acl_live_grant "
                "ON t_resource_acl "
                "(tenant_id, subject_type, subject_id, resource_type, resource_id) "
                "WHERE deleted = 0",
            ),
            (
                "t_index_sync_job",
                {"status", "next_retry_time"},
                "CREATE INDEX IF NOT EXISTS idx_sync_job_status "
                "ON t_index_sync_job (status, next_retry_time)",
            ),
            (
                "t_ingestion_task",
                {"tenant_id", "status", "create_time"},
                "CREATE INDEX IF NOT EXISTS idx_ingestion_task_monitor "
                "ON t_ingestion_task (tenant_id, status, create_time)",
            ),
            (
                "t_ingestion_task",
                {"tenant_id", "idempotency_key"},
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_task_idempotency "
                "ON t_ingestion_task (tenant_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL AND deleted = 0",
            ),
        )
        for table_name, required_columns, ddl in index_specs:
            current_columns = await conn.run_sync(
                lambda sync_conn, name=table_name: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns(name)
                }
            )
            if required_columns.issubset(current_columns):
                await conn.execute(text(ddl))

    return added_columns
