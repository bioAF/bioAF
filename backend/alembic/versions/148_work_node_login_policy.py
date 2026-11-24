"""Record each Work Node's login key fingerprint, SSH policy revision and sync route.

Revision ID: 148
Revises: 147
Create Date: 2026-11-24

Plan 03B. Nullable, nothing backfilled: nodes launched earlier have no recorded login binding,
which the key-replacement guard treats as possibly dependent while they may still exist.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "148"
down_revision = "147"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compute_sessions", sa.Column("login_key_fingerprint", sa.String(100), nullable=True))
    op.add_column("compute_sessions", sa.Column("ssh_policy_revision", sa.Integer(), nullable=True))
    op.add_column("compute_sessions", sa.Column("sync_route", sa.String(20), nullable=True))


def downgrade() -> None:
    for column in ("sync_route", "ssh_policy_revision", "login_key_fingerprint"):
        op.drop_column("compute_sessions", column)
