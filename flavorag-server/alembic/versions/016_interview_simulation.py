"""Interview materials, sessions, answers, scoring, and profile.

Revision ID: 016
Revises: 015
Create Date: 2026-07-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "t_interview_material",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("user_id", sa.String(20), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(256)),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "kind", name="uq_interview_material_user_kind"),
    )
    op.create_index(
        "idx_interview_material_tenant_user",
        "t_interview_material",
        ["tenant_id", "user_id"],
    )

    op.create_table(
        "t_interview_session",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("user_id", sa.String(20), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("conversation_id", sa.String(20)),
        sa.Column("kb_id", sa.String(20)),
        sa.Column("kb_name", sa.String(128)),
        sa.Column("target_role", sa.String(128)),
        sa.Column("difficulty", sa.String(16), nullable=False, server_default="senior"),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("resume_hash", sa.String(64)),
        sa.Column("jd_hash", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("overall_score", sa.Float()),
        sa.Column("dimension_scores", sa.JSON()),
        sa.Column("role_fit_breakdown", sa.JSON()),
        sa.Column("summary", sa.Text()),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_interview_session_user_status",
        "t_interview_session",
        ["user_id", "status"],
    )

    op.create_table(
        "t_interview_question",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("interview_id", sa.String(20), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("follow_up", sa.Text()),
        sa.Column("rubric", sa.JSON()),
        sa.Column("source", sa.JSON()),
        sa.Column("agent_generated", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "interview_id",
            "sequence",
            name="uq_interview_question_sequence",
        ),
    )
    op.create_index(
        "idx_interview_question_interview",
        "t_interview_question",
        ["interview_id", "sequence"],
    )

    op.create_table(
        "t_interview_answer",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("interview_id", sa.String(20), nullable=False),
        sa.Column("question_id", sa.String(20), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("skipped", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("score", sa.Float()),
        sa.Column("dimension_scores", sa.JSON()),
        sa.Column("analysis", sa.Text()),
        sa.Column("strengths", sa.JSON()),
        sa.Column("improvements", sa.JSON()),
        sa.Column("reference_points", sa.JSON()),
        sa.Column("answered_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "interview_id",
            "question_id",
            name="uq_interview_answer_question",
        ),
    )

    op.create_table(
        "t_interview_profile",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("user_id", sa.String(20), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("dimension_scores", sa.JSON()),
        sa.Column("overall_score", sa.Float()),
        sa.Column("previous_overall_score", sa.Float()),
        sa.Column("delta", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trend", sa.String(16), nullable=False, server_default="stable"),
        sa.Column("interview_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_interview_id", sa.String(20)),
        sa.Column("target_role", sa.String(128)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_interview_profile_user"),
    )
    op.create_index(
        "idx_interview_profile_tenant_user",
        "t_interview_profile",
        ["tenant_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_table("t_interview_profile")
    op.drop_table("t_interview_answer")
    op.drop_table("t_interview_question")
    op.drop_table("t_interview_session")
    op.drop_table("t_interview_material")

