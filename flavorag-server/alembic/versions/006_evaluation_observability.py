"""Persist retrieval evaluation runs and quality gates.

Revision ID: 006
Revises: 005
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "t_evaluation_run",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("kb_id", sa.String(20), nullable=False),
        sa.Column("kb_name", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("gate_status", sa.String(16), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON()),
        sa.Column("slices_json", sa.JSON()),
        sa.Column("gates_json", sa.JSON()),
        sa.Column("baseline_run_id", sa.String(20)),
        sa.Column("deltas_json", sa.JSON()),
        sa.Column("results_json", sa.JSON()),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("created_by", sa.String(20), nullable=False),
        sa.Column("create_time", sa.DateTime()),
        if_not_exists=True,
    )
    op.create_index(
        "idx_evaluation_run_trend",
        "t_evaluation_run",
        ["tenant_id", "kb_id", "status", "create_time"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_evaluation_run_trend", table_name="t_evaluation_run")
    op.drop_table("t_evaluation_run")
