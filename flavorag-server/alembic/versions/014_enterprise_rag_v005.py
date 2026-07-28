"""Enterprise RAG v0.0.5 generations, repair queue and idempotency.

Revision ID: 014
Revises: 013
Create Date: 2026-07-28
"""

from typing import Sequence, Union

from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    statements = [
        "ALTER TABLE t_knowledge_base ADD COLUMN IF NOT EXISTS active_collection_name VARCHAR(128)",
        "ALTER TABLE t_knowledge_base ADD COLUMN IF NOT EXISTS active_index_generation VARCHAR(32) NOT NULL DEFAULT 'v1'",
        "ALTER TABLE t_knowledge_document ADD COLUMN IF NOT EXISTS active_generation VARCHAR(32) NOT NULL DEFAULT 'v1'",
        "ALTER TABLE t_knowledge_document ADD COLUMN IF NOT EXISTS pending_generation VARCHAR(32)",
        "ALTER TABLE t_knowledge_chunk ADD COLUMN IF NOT EXISTS generation VARCHAR(32) NOT NULL DEFAULT 'v1'",
        "ALTER TABLE t_knowledge_chunk ADD COLUMN IF NOT EXISTS index_status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'",
        "ALTER TABLE t_knowledge_asset ADD COLUMN IF NOT EXISTS generation VARCHAR(32) NOT NULL DEFAULT 'v1'",
        "ALTER TABLE t_knowledge_asset ADD COLUMN IF NOT EXISTS index_status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'",
        "ALTER TABLE t_ingestion_job ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(64)",
        "ALTER TABLE t_ingestion_job ADD COLUMN IF NOT EXISTS generation VARCHAR(32) NOT NULL DEFAULT 'v1'",
        "ALTER TABLE t_evaluation_run ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE t_evaluation_run ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(64)",
        "ALTER TABLE t_evaluation_run ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP",
        "ALTER TABLE t_evaluation_run ADD COLUMN IF NOT EXISTS next_retry_time TIMESTAMP",
        "ALTER TABLE t_evaluation_run ADD COLUMN IF NOT EXISTS error_message TEXT",
        "ALTER TABLE t_batch_import_job ADD COLUMN IF NOT EXISTS config_json JSONB",
        "ALTER TABLE t_batch_import_job ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE t_batch_import_job ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(64)",
        "ALTER TABLE t_batch_import_job ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP",
        "ALTER TABLE t_batch_import_job ADD COLUMN IF NOT EXISTS next_retry_time TIMESTAMP",
        "ALTER TABLE t_batch_import_file ADD COLUMN IF NOT EXISTS source_location VARCHAR(1024)",
    ]
    for statement in statements:
        op.execute(statement)
    op.execute(
        "UPDATE t_ingestion_job SET idempotency_key = md5(id || ':' || doc_id) "
        "WHERE idempotency_key IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_ingestion_job_idempotency "
        "ON t_ingestion_job(idempotency_key)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_knowledge_index_generation (
            id VARCHAR(20) PRIMARY KEY,
            kb_id VARCHAR(20) NOT NULL,
            generation VARCHAR(32) NOT NULL,
            collection_name VARCHAR(128) NOT NULL,
            embedding_model VARCHAR(128) NOT NULL,
            embedding_dim INTEGER NOT NULL,
            parser_version VARCHAR(64),
            chunker_version VARCHAR(64),
            status VARCHAR(16) NOT NULL DEFAULT 'BUILDING',
            expected_chunks INTEGER DEFAULT 0,
            indexed_chunks INTEGER DEFAULT 0,
            activated_at TIMESTAMP,
            error_message TEXT,
            created_by VARCHAR(20) NOT NULL DEFAULT 'system',
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted SMALLINT DEFAULT 0,
            UNIQUE(kb_id, generation)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_index_repair_job (
            id VARCHAR(20) PRIMARY KEY,
            kb_id VARCHAR(20) NOT NULL,
            doc_id VARCHAR(20) NOT NULL,
            generation VARCHAR(32) NOT NULL,
            channel VARCHAR(24) NOT NULL,
            operation VARCHAR(24) NOT NULL DEFAULT 'UPSERT',
            status VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            next_retry_time TIMESTAMP,
            last_error TEXT,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted SMALLINT DEFAULT 0
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_active_generation "
        "ON t_knowledge_chunk(doc_id, generation, index_status, deleted)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_asset_active_generation "
        "ON t_knowledge_asset(doc_id, generation, index_status, deleted)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_evaluation_run_queue "
        "ON t_evaluation_run(status, next_retry_time, create_time)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_index_repair_status "
        "ON t_index_repair_job(status, next_retry_time, create_time)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS t_index_repair_job")
    op.execute("DROP TABLE IF EXISTS t_knowledge_index_generation")
