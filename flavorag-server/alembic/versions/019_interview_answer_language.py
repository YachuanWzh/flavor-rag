"""Persist the programming language used for algorithm answers.

Revision ID: 019
Revises: 018
Create Date: 2026-07-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_interview_answer",
        sa.Column("answer_language", sa.String(16)),
    )


def downgrade() -> None:
    op.drop_column("t_interview_answer", "answer_language")
