"""pipeline_runs: store the catalog key a catalog launch used.

Revision ID: 130
Revises: 129
Create Date: 2026-11-03

pipeline_name holds the catalog entry's display name, which reproduction was passing back as the
key, so a pipeline whose name and key differ (bioAF System Test / bioaf-system-test) could not be
reproduced. Nullable with no backfill: a historical row is reproducible only when its name is
exactly a catalog key, and a display name is never matched.
"""

import sqlalchemy as sa
from alembic import op

revision = "130"
down_revision = "129"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("pipeline_key", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_runs", "pipeline_key")
