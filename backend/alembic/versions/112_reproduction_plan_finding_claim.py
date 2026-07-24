"""ReproductionPlan.finding_claim_json (lit_validation B4 / Level-3 finding concordance).

Revision ID: 112
Revises: 111
Create Date: 2026-07-24

Additive only. Adds the paper's own deposited result set (its DEG table / DA peak list), normalized
to a directional FindingSet and confirmed by the human at the C1 gate. Nullable JSONB, mirroring
app/models/reproduction_plan.py; None until confirmed. This is the ground truth Level-3 concordance
scores our reproduction against.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "112"
down_revision = "111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reproduction_plans",
        sa.Column("finding_claim_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reproduction_plans", "finding_claim_json")
