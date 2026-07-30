"""Interview focus preference and explicit abandonment lifecycle.

Revision ID: 017
Revises: 016
Create Date: 2026-07-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_interview_session",
        sa.Column("user_focus", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("t_interview_session", "user_focus")
