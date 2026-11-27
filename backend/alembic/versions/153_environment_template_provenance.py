"""Explicit provenance for system-provided environment templates (plan 03.5B).

Revision ID: 153
Revises: 152
Create Date: 2026-11-25

Nullable, nothing backfilled: an existing definition is not known to be untouched (its name and
administrator creator prove nothing), so legacy environments stay user-managed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "153"
down_revision = "152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("environments", sa.Column("template_key", sa.String(64), nullable=True))
    op.add_column("environments", sa.Column("template_revision", sa.Integer(), nullable=True))
    op.add_column("environment_versions", sa.Column("template_revision", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("environment_versions", "template_revision")
    op.drop_column("environments", "template_revision")
    op.drop_column("environments", "template_key")
