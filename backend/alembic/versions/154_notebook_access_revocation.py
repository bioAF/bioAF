"""When a user's notebook gateway credentials stop being honored (plan 03.5A).

Revision ID: 154
Revises: 153
Create Date: 2026-11-26

Signing out of bioAF sets it; the gateway refuses credentials issued before it. Null (every existing
user) revokes nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "154"
down_revision = "153"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("notebook_access_revoked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "notebook_access_revoked_at")
