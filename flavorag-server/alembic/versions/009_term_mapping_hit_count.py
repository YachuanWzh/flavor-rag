"""Add hit_count to t_query_term_mapping for usage statistics.

Revision ID: 009
Revises: 008
Create Date: 2026-07-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE t_query_term_mapping "
        "ADD COLUMN IF NOT EXISTS hit_count INTEGER DEFAULT 0"
    )


def downgrade() -> None:
    op.drop_column("t_query_term_mapping", "hit_count")
