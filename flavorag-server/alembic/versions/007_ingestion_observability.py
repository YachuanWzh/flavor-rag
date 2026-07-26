"""Add tenant-scoped ingestion observability and resilience fields.

Revision ID: 007
Revises: 006
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    statements = (
        "ALTER TABLE t_ingestion_pipeline ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'",
        "ALTER TABLE t_ingestion_pipeline ADD COLUMN IF NOT EXISTS enabled SMALLINT NOT NULL DEFAULT 1",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS kb_id VARCHAR(20)",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS doc_id VARCHAR(20)",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS trace_id VARCHAR(32)",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS parent_task_id VARCHAR(20)",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS total_duration_ms BIGINT",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS sla_ms BIGINT NOT NULL DEFAULT 300000",
        "ALTER TABLE t_ingestion_task ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP",
        "ALTER TABLE t_ingestion_task_node ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE t_ingestion_task_node ADD COLUMN IF NOT EXISTS started_at TIMESTAMP",
        "ALTER TABLE t_ingestion_task_node ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP",
    )
    for statement in statements:
        op.execute(statement)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ingestion_task_monitor "
        "ON t_ingestion_task (tenant_id, status, create_time)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_task_idempotency "
        "ON t_ingestion_task (tenant_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL AND deleted = 0"
    )


def downgrade() -> None:
    op.drop_index("uq_ingestion_task_idempotency", table_name="t_ingestion_task")
    op.drop_index("idx_ingestion_task_monitor", table_name="t_ingestion_task")
    for table, columns in (
        ("t_ingestion_task_node", ("completed_at", "started_at", "attempt")),
        (
            "t_ingestion_task",
            (
                "heartbeat_at", "sla_ms", "total_duration_ms", "attempt",
                "parent_task_id", "idempotency_key", "trace_id", "doc_id",
                "kb_id", "tenant_id",
            ),
        ),
        ("t_ingestion_pipeline", ("enabled", "tenant_id")),
    ):
        for column in columns:
            op.drop_column(table, column)
