"""Add metadata for runnable interview algorithm questions.

Revision ID: 018
Revises: 017
Create Date: 2026-07-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("t_interview_question", sa.Column("metadata", sa.JSON()))


def downgrade() -> None:
    op.drop_column("t_interview_question", "metadata")
