"""Create t_user_profile table for mem0 long-term memory + user profiling.

Seven dimensions:
  1. Basic info (FK to t_user)
  2. Professional domain (LLM-extracted)
  3. Intent preference distribution (auto-stats)
  4. Knowledge-base preference (auto-stats)
  5. Query-style metrics (auto-stats)
  6. Feedback signals (auto-stats)
  7. mem0 memory facts summary

Revision ID: 013
Revises: 012
Create Date: 2026-07-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_user_profile (
            id                  VARCHAR(20) PRIMARY KEY,
            user_id             VARCHAR(20) NOT NULL UNIQUE,
            tenant_id           VARCHAR(64) NOT NULL DEFAULT 'default',

            -- Dimension 2: Professional domain (LLM-extracted)
            domains             JSON,
            expertise_level     VARCHAR(16),
            domain_summary      TEXT,

            -- Dimension 3: Intent preference distribution
            intent_distribution JSON,

            -- Dimension 4: Knowledge-base preference
            preferred_kbs       JSON,
            preferred_doc_types JSON,

            -- Dimension 5: Query-style metrics
            avg_query_length    FLOAT,
            deep_thinking_rate  FLOAT,
            graph_rag_rate      FLOAT,
            hyde_rate           FLOAT,

            -- Dimension 6: Feedback signals
            thumbs_up_count     INT DEFAULT 0,
            thumbs_down_count   INT DEFAULT 0,
            follow_up_rate      FLOAT,
            satisfaction_topics JSON,

            -- Dimension 7: mem0 memory facts
            mem0_facts_count    INT DEFAULT 0,
            mem0_last_sync      TIMESTAMP,

            -- Metadata
            total_queries       INT DEFAULT 0,
            total_conversations INT DEFAULT 0,
            last_active_time    TIMESTAMP,
            profile_version     INT DEFAULT 1,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_profile_tenant ON t_user_profile (tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS t_user_profile")
