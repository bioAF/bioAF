"""Per-session IDE secret for protected notebook access (plan 03.5A).

Revision ID: 151
Revises: 150
Create Date: 2026-11-25

Encrypted at rest (EncryptedString, Fernet text). Null for sessions launched before the gateway.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "151"
down_revision = "150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compute_sessions", sa.Column("notebook_access_secret", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("compute_sessions", "notebook_access_secret")
