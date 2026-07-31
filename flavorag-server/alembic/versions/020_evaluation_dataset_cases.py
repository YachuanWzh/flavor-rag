"""Persist production questions as base and golden evaluation cases.

Revision ID: 020
Revises: 019
Create Date: 2026-07-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "t_evaluation_dataset_case",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("source_question_id", sa.String(20), nullable=False),
        sa.Column("source_answer_id", sa.String(20)),
        sa.Column("user_id", sa.String(20), nullable=False),
        sa.Column("conversation_id", sa.String(20), nullable=False),
        sa.Column("case_type", sa.String(16), nullable=False, server_default="base"),
        sa.Column("review_status", sa.String(24), nullable=False, server_default="generated"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("expected_doc_ids", sa.JSON(), nullable=False),
        sa.Column("retrieved_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("retrieved_doc_ids", sa.JSON(), nullable=False),
        sa.Column("knowledge_base_ids", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, server_default="production"),
        sa.Column("difficulty", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("answerable", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("active", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("feedback_vote", sa.SmallInteger()),
        sa.Column("feedback_reason", sa.String(255)),
        sa.Column("feedback_comment", sa.String(1024)),
        sa.Column("promoted_by", sa.String(20)),
        sa.Column("promoted_at", sa.DateTime()),
        sa.Column("create_time", sa.DateTime()),
        sa.Column("update_time", sa.DateTime()),
        sa.Column("deleted", sa.SmallInteger(), server_default="0"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_question_id",
            name="uq_evaluation_case_source_question",
        ),
    )
    op.create_index(
        "idx_evaluation_case_tenant_status",
        "t_evaluation_dataset_case",
        ["tenant_id", "case_type", "active", "create_time"],
    )
    op.create_index(
        "idx_evaluation_case_user",
        "t_evaluation_dataset_case",
        ["tenant_id", "user_id", "create_time"],
    )


def downgrade() -> None:
    op.drop_index("idx_evaluation_case_user", table_name="t_evaluation_dataset_case")
    op.drop_index("idx_evaluation_case_tenant_status", table_name="t_evaluation_dataset_case")
    op.drop_table("t_evaluation_dataset_case")

