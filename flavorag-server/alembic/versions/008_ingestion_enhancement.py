"""Add content_hash to documents, batch import tables, and ingestion enhancements.

Revision ID: 008
Revises: 007
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add content_hash to t_knowledge_document for dedup + incremental indexing
    op.execute(
        "ALTER TABLE t_knowledge_document ADD COLUMN IF NOT EXISTS "
        "content_hash VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_document_content_hash "
        "ON t_knowledge_document (kb_id, content_hash) "
        "WHERE content_hash IS NOT NULL AND deleted = 0"
    )

    # 2. Create batch import job table
    op.execute(
        "CREATE TABLE IF NOT EXISTS t_batch_import_job ("
        "  id VARCHAR(20) PRIMARY KEY,"
        "  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
        "  kb_id VARCHAR(20) NOT NULL,"
        "  total_files INTEGER NOT NULL DEFAULT 0,"
        "  completed_files INTEGER NOT NULL DEFAULT 0,"
        "  failed_files INTEGER NOT NULL DEFAULT 0,"
        "  skipped_duplicates INTEGER NOT NULL DEFAULT 0,"
        "  status VARCHAR(16) NOT NULL DEFAULT 'pending',"
        "  file_results JSON,"
        "  error_message TEXT,"
        "  created_by VARCHAR(20) NOT NULL,"
        "  create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "  update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "  deleted SMALLINT DEFAULT 0"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_batch_import_job_tenant "
        "ON t_batch_import_job (tenant_id, kb_id, status)"
    )

    # 3. Create batch import file record table
    op.execute(
        "CREATE TABLE IF NOT EXISTS t_batch_import_file ("
        "  id VARCHAR(20) PRIMARY KEY,"
        "  job_id VARCHAR(20) NOT NULL,"
        "  file_name VARCHAR(512) NOT NULL,"
        "  file_size BIGINT,"
        "  file_type VARCHAR(16),"
        "  status VARCHAR(16) NOT NULL DEFAULT 'pending',"
        "  doc_id VARCHAR(20),"
        "  chunk_count INTEGER DEFAULT 0,"
        "  error_message TEXT,"
        "  create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_batch_import_file_job "
        "ON t_batch_import_file (job_id)"
    )


def downgrade() -> None:
    op.drop_index("idx_batch_import_file_job", table_name="t_batch_import_file")
    op.drop_table("t_batch_import_file")
    op.drop_index("idx_batch_import_job_tenant", table_name="t_batch_import_job")
    op.drop_table("t_batch_import_job")
    op.drop_index(
        "idx_knowledge_document_content_hash",
        table_name="t_knowledge_document",
    )
    op.drop_column("t_knowledge_document", "content_hash")
