"""Add hyde_doc / hyde_meta to t_message (HyDE retrieval metadata).

These columns exist on the ORM model (Message) and were auto-created on
SQLite by the schema initializer, but were never added to PostgreSQL by a
migration, causing "column t_message.hyde_doc does not exist" on chat.

Revision ID: 011
Revises: 010
Create Date: 2026-07-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE t_message ADD COLUMN IF NOT EXISTS hyde_doc TEXT")
    op.execute("ALTER TABLE t_message ADD COLUMN IF NOT EXISTS hyde_meta JSON")


def downgrade() -> None:
    op.drop_column("t_message", "hyde_meta")
    op.drop_column("t_message", "hyde_doc")
