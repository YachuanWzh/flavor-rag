"""Hyperparameter config table for runtime overrides.

Revision ID: 015
Revises: 014
Create Date: 2026-07-28
"""

from typing import Sequence, Union

from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS t_hyperparameter_config (
            id VARCHAR(20) PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
            key VARCHAR(128) NOT NULL,
            value TEXT NOT NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted SMALLINT DEFAULT 0,
            UNIQUE(tenant_id, key)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_hyperparam_tenant "
        "ON t_hyperparameter_config(tenant_id, key)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS t_hyperparameter_config")
