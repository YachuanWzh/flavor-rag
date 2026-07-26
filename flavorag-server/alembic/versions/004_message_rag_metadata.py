"""Persist message-level Agentic RAG and Graph RAG metadata.

Revision ID: 004
Revises: 003
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE t_message ADD COLUMN IF NOT EXISTS agent_steps JSON")
    op.execute("ALTER TABLE t_message ADD COLUMN IF NOT EXISTS rag_modes JSON")
    op.execute(
        "ALTER TABLE t_message "
        "ADD COLUMN IF NOT EXISTS retrieval_channels JSON"
    )


def downgrade() -> None:
    op.drop_column("t_message", "retrieval_channels")
    op.drop_column("t_message", "rag_modes")
    op.drop_column("t_message", "agent_steps")
