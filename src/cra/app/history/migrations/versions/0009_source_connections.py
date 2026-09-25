"""connected sources kept per account

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_connections",
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("sealed", sa.Text(), nullable=False),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("source_connections")
