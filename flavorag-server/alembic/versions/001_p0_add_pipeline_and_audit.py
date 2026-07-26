"""P0 enterprise features — add audit, schedule, pipeline, chunk_log tables.

Revision ID: 001
Revises: None
Create Date: 2026-07-25
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Add pipeline_id column to t_knowledge_base ---
    op.execute(
        "ALTER TABLE t_knowledge_base "
        "ADD COLUMN IF NOT EXISTS pipeline_id VARCHAR(20)"
    )

    # --- t_biz_change_log ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_biz_change_log (
            id               VARCHAR(20)  PRIMARY KEY,
            biz_type         VARCHAR(64)  NOT NULL,
            biz_id           VARCHAR(64)  NOT NULL,
            operation_type   VARCHAR(32)  NOT NULL,
            action_desc      VARCHAR(512),
            before_snapshot  JSON,
            after_snapshot   JSON,
            change_diff      JSON,
            operator_id      VARCHAR(64),
            operator_name    VARCHAR(128),
            operator_role    VARCHAR(64),
            success          SMALLINT     NOT NULL DEFAULT 1,
            error_message    TEXT,
            class_name       VARCHAR(255),
            method_name      VARCHAR(255),
            ip               VARCHAR(64),
            user_agent       VARCHAR(512),
            create_time      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- t_knowledge_document_schedule ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_knowledge_document_schedule (
            id                VARCHAR(20)  PRIMARY KEY,
            doc_id            VARCHAR(20)  NOT NULL UNIQUE,
            kb_id             VARCHAR(20)  NOT NULL,
            cron_expr         VARCHAR(64),
            enabled           SMALLINT     DEFAULT 0,
            next_run_time     TIMESTAMP,
            last_run_time     TIMESTAMP,
            last_success_time TIMESTAMP,
            last_status       VARCHAR(16),
            last_error        VARCHAR(512),
            last_etag         VARCHAR(256),
            last_modified     VARCHAR(256),
            last_content_hash VARCHAR(128),
            lock_owner        VARCHAR(128),
            lock_until        TIMESTAMP,
            create_time       TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
            update_time       TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- t_knowledge_document_schedule_exec ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_knowledge_document_schedule_exec (
            id            VARCHAR(20)  PRIMARY KEY,
            schedule_id   VARCHAR(20)  NOT NULL,
            doc_id        VARCHAR(20)  NOT NULL,
            kb_id         VARCHAR(20)  NOT NULL,
            status        VARCHAR(16)  NOT NULL,
            message       VARCHAR(512),
            start_time    TIMESTAMP,
            end_time      TIMESTAMP,
            file_name     VARCHAR(512),
            file_size     BIGINT,
            content_hash  VARCHAR(128),
            etag          VARCHAR(256),
            last_modified VARCHAR(256),
            create_time   TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
            update_time   TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- t_knowledge_document_chunk_log ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_knowledge_document_chunk_log (
            id                 VARCHAR(20)  PRIMARY KEY,
            doc_id             VARCHAR(20)  NOT NULL,
            status             VARCHAR(16)  NOT NULL,
            process_mode       VARCHAR(16),
            chunk_strategy     VARCHAR(16),
            pipeline_id        VARCHAR(20),
            extract_duration   BIGINT,
            chunk_duration     BIGINT,
            embed_duration     BIGINT,
            persist_duration   BIGINT,
            total_duration     BIGINT,
            chunk_count        INTEGER,
            error_message      TEXT,
            start_time         TIMESTAMP,
            end_time           TIMESTAMP,
            create_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- t_ingestion_pipeline ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_ingestion_pipeline (
            id          VARCHAR(20)  PRIMARY KEY,
            name        VARCHAR(128) NOT NULL,
            description VARCHAR(512),
            created_by  VARCHAR(20)  NOT NULL,
            updated_by  VARCHAR(20),
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted     SMALLINT    DEFAULT 0
        )
    """)

    # --- t_ingestion_pipeline_node ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_ingestion_pipeline_node (
            id             VARCHAR(20)  PRIMARY KEY,
            pipeline_id    VARCHAR(20)  NOT NULL,
            node_id        VARCHAR(64)  NOT NULL,
            node_type      VARCHAR(32)  NOT NULL,
            next_node_id   VARCHAR(64),
            settings_json  JSON,
            condition_json JSON,
            created_by     VARCHAR(20)  NOT NULL,
            updated_by     VARCHAR(20),
            create_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted        SMALLINT    DEFAULT 0
        )
    """)

    # --- t_ingestion_task ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_ingestion_task (
            id               VARCHAR(20)   PRIMARY KEY,
            pipeline_id      VARCHAR(20)   NOT NULL,
            source_type      VARCHAR(32)   NOT NULL,
            source_location  VARCHAR(1024) NOT NULL,
            source_file_name VARCHAR(512),
            status           VARCHAR(16)   DEFAULT 'pending',
            chunk_count      INTEGER       DEFAULT 0,
            error_message    TEXT,
            logs_json        JSON,
            metadata_json    JSON,
            started_at       TIMESTAMP,
            completed_at     TIMESTAMP,
            created_by       VARCHAR(20)   NOT NULL,
            updated_by       VARCHAR(20),
            create_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted          SMALLINT     DEFAULT 0
        )
    """)

    # --- t_ingestion_task_node ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_ingestion_task_node (
            id            VARCHAR(20)  PRIMARY KEY,
            task_id       VARCHAR(20)  NOT NULL,
            pipeline_id   VARCHAR(20)  NOT NULL,
            node_id       VARCHAR(64)  NOT NULL,
            node_type     VARCHAR(32)  NOT NULL,
            node_order    INTEGER      DEFAULT 0,
            status        VARCHAR(16)  DEFAULT 'pending',
            duration_ms   BIGINT,
            message       VARCHAR(512),
            error_message TEXT,
            output_json   JSON,
            create_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted       SMALLINT    DEFAULT 0
        )
    """)


def downgrade() -> None:
    op.drop_table("t_ingestion_task_node")
    op.drop_table("t_ingestion_task")
    op.drop_table("t_ingestion_pipeline_node")
    op.drop_table("t_ingestion_pipeline")
    op.drop_table("t_knowledge_document_chunk_log")
    op.drop_table("t_knowledge_document_schedule_exec")
    op.drop_table("t_knowledge_document_schedule")
    op.drop_table("t_biz_change_log")
    # SQLite doesn't support DROP COLUMN, so skip pipeline_id removal
