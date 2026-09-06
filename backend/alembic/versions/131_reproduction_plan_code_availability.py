"""reproduction_plans.code_availability: where the paper said its analysis code lives.

Revision ID: 131
Revises: 130
Create Date: 2026-09-05

plan_7 step 3. "Check if they have code available so we can just plug and chug our way through" is
one of the seven things the bioinformaticians asked for, and the feature had nowhere to put the
answer: no column, and no mention of github or zenodo anywhere in the literature services.

Stores and displays; executes nothing. Running a stranger's repository against a deposited matrix
is a sandboxing and provenance problem of its own and is explicitly out of plan_7's scope. What the
column buys now is that the C1 gate can show a scientist where the authors' own code is before they
authorise a reproduction, and that a divergence can be attributed to a named difference ("the paper
published its DESeq2 script and we used ours") rather than left unexplained, which is the same job
tools_json does for the aligner.

Nullable with no backfill, exactly as 127 did for library_strategy: NULL means "planned before this
column existed" and is honestly different from the [] an extraction writes when it looked and the
paper named no code.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "131"
down_revision = "130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reproduction_plans",
        sa.Column("code_availability_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reproduction_plans", "code_availability_json")
