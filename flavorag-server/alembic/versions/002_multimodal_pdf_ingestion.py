"""Add structured PDF chunk metadata and multimodal asset persistence.

Revision ID: 002
Revises: 001
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE t_knowledge_chunk "
        "ADD COLUMN IF NOT EXISTS embedding_content TEXT"
    )
    op.execute(
        "ALTER TABLE t_knowledge_chunk "
        "ADD COLUMN IF NOT EXISTS block_type VARCHAR(32)"
    )
    op.execute(
        "ALTER TABLE t_knowledge_chunk "
        "ADD COLUMN IF NOT EXISTS page_start INTEGER"
    )
    op.execute(
        "ALTER TABLE t_knowledge_chunk "
        "ADD COLUMN IF NOT EXISTS page_end INTEGER"
    )
    op.execute(
        "ALTER TABLE t_knowledge_chunk "
        "ADD COLUMN IF NOT EXISTS bbox_json JSON"
    )
    op.execute(
        "ALTER TABLE t_knowledge_chunk "
        "ADD COLUMN IF NOT EXISTS metadata_json JSON"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_knowledge_asset (
            id            VARCHAR(32) PRIMARY KEY,
            kb_id         VARCHAR(20)  NOT NULL,
            doc_id        VARCHAR(20)  NOT NULL,
            asset_type    VARCHAR(32)  NOT NULL DEFAULT 'IMAGE',
            mime_type     VARCHAR(128) NOT NULL,
            file_name     VARCHAR(512),
            file_size     BIGINT,
            content_hash  VARCHAR(64)  NOT NULL,
            storage_key   VARCHAR(1024) NOT NULL,
            storage_url   VARCHAR(2048) NOT NULL,
            page_no       INTEGER,
            bbox_json     JSON,
            description   TEXT,
            metadata_json JSON,
            created_by    VARCHAR(20) NOT NULL,
            updated_by    VARCHAR(20),
            create_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted       SMALLINT DEFAULT 0
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_asset_doc_id "
        "ON t_knowledge_asset (doc_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_asset_hash "
        "ON t_knowledge_asset (content_hash)"
    )


def downgrade() -> None:
    op.drop_table("t_knowledge_asset")
    op.execute("ALTER TABLE t_knowledge_chunk DROP COLUMN metadata_json")
    op.execute("ALTER TABLE t_knowledge_chunk DROP COLUMN bbox_json")
    op.execute("ALTER TABLE t_knowledge_chunk DROP COLUMN page_end")
    op.execute("ALTER TABLE t_knowledge_chunk DROP COLUMN page_start")
    op.execute("ALTER TABLE t_knowledge_chunk DROP COLUMN block_type")
    op.execute("ALTER TABLE t_knowledge_chunk DROP COLUMN embedding_content")
