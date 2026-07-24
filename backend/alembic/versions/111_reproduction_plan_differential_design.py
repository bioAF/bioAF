"""ReproductionPlan.differential_design_json (lit_validation B2e / Level-3 finding concordance).

Revision ID: 111
Revises: 110
Create Date: 2026-07-24

Additive only. Adds the structured differential design the extractor captures and the human ratifies
at the C1 gate (contrasts, condition->sample map, significance thresholds). Nullable JSONB, mirroring
app/models/reproduction_plan.py; None for a QC-only paper with no differential finding. This is the
finding to reproduce, distinct from parameters_json (the nf-core pipeline params).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "111"
down_revision = "110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reproduction_plans",
        sa.Column("differential_design_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reproduction_plans", "differential_design_json")
