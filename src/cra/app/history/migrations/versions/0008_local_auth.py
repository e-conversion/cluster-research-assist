"""password accounts, one-time password links and access requests

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email", sa.String(320), nullable=False, server_default=""),
    )
    op.create_table(
        "local_credentials",
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "password_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(10), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "access_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("issuer", sa.String(500), nullable=False),
        sa.Column("sub", sa.String(500), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("home_organization", sa.String(200), nullable=False),
        sa.Column("organization_name", sa.String(200), nullable=False),
        sa.Column("group_smid", sa.String(100), nullable=False),
        sa.Column("group_name", sa.String(300), nullable=False),
        sa.Column("profile_url", sa.String(500), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by", sa.String(200), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=False),
        sa.UniqueConstraint("issuer", "sub"),
    )


def downgrade() -> None:
    op.drop_table("access_requests")
    op.drop_table("password_tokens")
    op.drop_table("local_credentials")
    op.drop_column("users", "email")
