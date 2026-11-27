"""What a notebook EnvironmentVersion's build proved under the runtime contract (plan 03.5B).

Revision ID: 152
Revises: 151
Create Date: 2026-11-25

Null for every existing version: images built before the contract are unverified until rebuilt.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "152"
down_revision = "151"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("environment_versions", sa.Column("notebook_capability", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("environment_versions", "notebook_capability")
