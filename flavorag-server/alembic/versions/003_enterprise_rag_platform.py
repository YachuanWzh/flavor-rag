"""Add tenant ACL, trace governance, conversation summary, and sync outbox.

Revision ID: 003
Revises: 002
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_tenant (
            id VARCHAR(20) PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            enabled SMALLINT DEFAULT 1,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_department (
            id VARCHAR(20) PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL,
            parent_id VARCHAR(20),
            name VARCHAR(128) NOT NULL,
            created_by VARCHAR(20) NOT NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted SMALLINT DEFAULT 0
        )
    """)
    additions = {
        "t_user": [
            ("tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("department_id", "VARCHAR(64)"),
        ],
        "t_conversation": [
            ("tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("summary", "TEXT"),
            ("summary_message_count", "INTEGER DEFAULT 0"),
        ],
        "t_knowledge_base": [
            ("tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("department_id", "VARCHAR(64)"),
            ("visibility", "VARCHAR(16) NOT NULL DEFAULT 'PRIVATE'"),
        ],
        "t_knowledge_document": [
            ("tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("department_id", "VARCHAR(64)"),
            ("visibility", "VARCHAR(16) NOT NULL DEFAULT 'INHERIT'"),
        ],
        "t_knowledge_chunk": [
            ("tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("department_id", "VARCHAR(64)"),
        ],
        "t_knowledge_asset": [
            ("tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("department_id", "VARCHAR(64)"),
        ],
        "t_rag_trace_run": [
            ("tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
            ("kb_id", "VARCHAR(20)"),
            ("rejection_reason", "VARCHAR(64)"),
            ("metadata_json", "JSON"),
        ],
    }
    for table_name, columns in additions.items():
        for name, ddl_type in columns:
            op.execute(
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN IF NOT EXISTS {name} {ddl_type}"
            )

    op.execute("""
        CREATE TABLE IF NOT EXISTS t_resource_acl (
            id VARCHAR(20) PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL,
            subject_type VARCHAR(24) NOT NULL,
            subject_id VARCHAR(64) NOT NULL,
            resource_type VARCHAR(24) NOT NULL,
            resource_id VARCHAR(20) NOT NULL,
            permission VARCHAR(16) NOT NULL DEFAULT 'READ',
            created_by VARCHAR(20) NOT NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted SMALLINT DEFAULT 0
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_index_sync_job (
            id VARCHAR(20) PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL,
            kb_id VARCHAR(20) NOT NULL,
            doc_id VARCHAR(20) NOT NULL,
            operation VARCHAR(24) NOT NULL,
            payload_json JSON,
            channel_status_json JSON,
            status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_retry_time TIMESTAMP,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted SMALLINT DEFAULT 0
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_acl_resource ON t_resource_acl "
        "(tenant_id, resource_type, resource_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_acl_subject ON t_resource_acl "
        "(tenant_id, subject_type, subject_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_job_status ON t_index_sync_job "
        "(status, next_retry_time)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_kb_tenant "
        "ON t_knowledge_base (tenant_id, deleted)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_tenant ON t_knowledge_document "
        "(tenant_id, kb_id, deleted)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_tenant ON t_knowledge_chunk "
        "(tenant_id, kb_id, doc_id, deleted, enabled)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_acl_live_grant ON t_resource_acl "
        "(tenant_id, subject_type, subject_id, resource_type, resource_id) "
        "WHERE deleted = 0"
    )


def downgrade() -> None:
    op.drop_table("t_index_sync_job")
    op.drop_table("t_resource_acl")
    op.drop_table("t_department")
