"""signing in to the outward MCP endpoint (OAuth): clients, pending requests,
and grants kept as tokens

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("info", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "oauth_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(64),
            sa.ForeignKey("oauth_clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("code_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("mcp_tokens") as batch:
        batch.add_column(sa.Column("client_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("refresh_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("access_expires_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_mcp_tokens_client_id",
            "oauth_clients",
            ["client_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_mcp_tokens_refresh_hash", ["refresh_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("mcp_tokens") as batch:
        batch.drop_index("ix_mcp_tokens_refresh_hash")
        batch.drop_constraint("fk_mcp_tokens_client_id", type_="foreignkey")
        batch.drop_column("access_expires_at")
        batch.drop_column("refresh_hash")
        batch.drop_column("client_id")
    op.drop_table("oauth_requests")
    op.drop_table("oauth_clients")
