"""the role an invited address starts with

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registered_emails",
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
    )


def downgrade() -> None:
    op.drop_column("registered_emails", "role")
