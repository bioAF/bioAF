"""Add is_global flag to files.

Revision ID: 069
Revises: 068

Distinguishes deliberately global files (org-scoped, no project/exp/sample)
from files that are merely unassociated. Both have all FK columns NULL,
so a flag is required to tell them apart in the UI.
"""

import sqlalchemy as sa
from alembic import op

revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("is_global", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("files", "is_global")
