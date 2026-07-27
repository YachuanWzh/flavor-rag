"""Give t_message.tenant_id a server default of 'default'.

The Message ORM model has no tenant_id column (SQLite's t_message never had
one), so INSERTs omit it.  The PostgreSQL schema declares the column NOT NULL
without a default, which made every message insert fail with a not-null
violation.  Adding a server default keeps NOT NULL while matching the
historical behaviour.

Revision ID: 012
Revises: 011
Create Date: 2026-07-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE t_message ALTER COLUMN tenant_id SET DEFAULT 'default'")
    op.execute("UPDATE t_message SET tenant_id = 'default' WHERE tenant_id IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE t_message ALTER COLUMN tenant_id DROP DEFAULT")
