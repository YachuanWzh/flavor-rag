"""Ingestion outbox job table for asynchronous document ingestion.

Revision ID: 010
Revises: 009
Create Date: 2026-07-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "t_ingestion_job",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("kb_id", sa.String(20), nullable=False),
        sa.Column("doc_id", sa.String(20), nullable=False),
        sa.Column("pipeline_id", sa.String(20)),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="file"),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column(
            "chunk_strategy",
            sa.String(32),
            nullable=False,
            server_default="FIXED_WINDOW",
        ),
        sa.Column("chunk_config_json", sa.JSON),
        sa.Column("operation", sa.String(24), nullable=False, server_default="INGEST"),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("next_retry_time", sa.DateTime),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("claimed_at", sa.DateTime),
        sa.Column("started_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("duration_ms", sa.BigInteger),
        sa.Column("chunk_count", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("created_by", sa.String(20), nullable=False),
        sa.Column("create_time", sa.DateTime),
        sa.Column("update_time", sa.DateTime),
        sa.Column("deleted", sa.SmallInteger, server_default="0"),
        if_not_exists=True,
    )
    op.create_index(
        "idx_ingestion_job_claim",
        "t_ingestion_job",
        ["status", "next_retry_time", "create_time"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_ingestion_job_doc",
        "t_ingestion_job",
        ["tenant_id", "doc_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_ingestion_job_doc", table_name="t_ingestion_job")
    op.drop_index("idx_ingestion_job_claim", table_name="t_ingestion_job")
    op.drop_table("t_ingestion_job")
