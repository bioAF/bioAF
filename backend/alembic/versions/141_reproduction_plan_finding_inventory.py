"""plan_8 section 2: the reviewed finding inventory, the Validation Scorecard's denominator.

``reproduction_plans.finding_inventory_json`` holds the paper's distinct computational findings, each
with the claims that establish it, its importance category under the fixed rubric (weighted rubric
version 1), its rationale and quote, and the criteria recorded before any result existed. Revisions
keep the one they replace in ``history``. NULL on a plan read before it, for which no score is derived.

Revision ID: 141
Revises: 140
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "141"
down_revision = "140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reproduction_plans", sa.Column("finding_inventory_json", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("reproduction_plans", "finding_inventory_json")
