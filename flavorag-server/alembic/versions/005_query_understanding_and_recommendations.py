"""Connect tenant-scoped query understanding and recommendations.

Revision ID: 005
Revises: 004
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE t_message ADD COLUMN IF NOT EXISTS recommended_questions JSON")
    op.execute(
        "ALTER TABLE t_intent_node "
        "ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'"
    )
    op.execute(
        "ALTER TABLE t_intent_node "
        "ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'KB'"
    )
    op.execute(
        "ALTER TABLE t_intent_node "
        "ADD COLUMN IF NOT EXISTS score_threshold INTEGER DEFAULT 30"
    )
    op.execute("ALTER TABLE t_intent_node ADD COLUMN IF NOT EXISTS examples JSON")
    op.execute(
        "ALTER TABLE t_intent_node ADD COLUMN IF NOT EXISTS mcp_tool_id VARCHAR(128)"
    )
    op.execute(
        "ALTER TABLE t_query_term_mapping "
        "ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'"
    )
    op.execute(
        "ALTER TABLE t_sample_question "
        "ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_intent_tenant "
        "ON t_intent_node (tenant_id, kb_id, enabled, deleted)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_term_mapping_tenant "
        "ON t_query_term_mapping (tenant_id, kb_id, enabled, deleted)"
    )


def downgrade() -> None:
    op.drop_column("t_sample_question", "tenant_id")
    op.drop_column("t_query_term_mapping", "tenant_id")
    op.drop_column("t_intent_node", "mcp_tool_id")
    op.drop_column("t_intent_node", "examples")
    op.drop_column("t_intent_node", "score_threshold")
    op.drop_column("t_intent_node", "kind")
    op.drop_column("t_intent_node", "tenant_id")
    op.drop_column("t_message", "recommended_questions")
