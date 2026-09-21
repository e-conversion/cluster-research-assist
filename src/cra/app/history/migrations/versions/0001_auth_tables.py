"""users, registered_emails, identities, sessions

Revision ID: 0001
Revises:
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "registered_emails",
        sa.Column("email", sa.String(320), primary_key=True),
        sa.Column(
            "user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "identities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("issuer", sa.String(500), nullable=False),
        sa.Column("sub", sa.String(500), nullable=False),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("home_organization", sa.String(200), nullable=False),
        sa.Column("bound_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("issuer", "sub"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE")
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    for table in ("sessions", "identities", "registered_emails", "users"):
        op.drop_table(table)
