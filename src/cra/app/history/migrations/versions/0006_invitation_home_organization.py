"""the home organisation an invitation may be claimed from

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registered_emails",
        sa.Column(
            "home_organization", sa.String(200), nullable=False, server_default=""
        ),
    )


def downgrade() -> None:
    op.drop_column("registered_emails", "home_organization")
