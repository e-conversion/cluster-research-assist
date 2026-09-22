"""user roles and the policy overrides

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
    )
    op.create_table(
        "policy",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("policy")
    op.drop_column("users", "role")
