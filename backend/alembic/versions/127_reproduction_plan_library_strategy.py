"""reproduction_plans.library_strategy: keep what the deposit said its data is.

Revision ID: 127
Revises: 126
Create Date: 2026-08-31

The extractor reads the scoped accession's declared `library_strategy` and hands
it to the mapper, which is what lets a deposit overrule a multi-assay paper's
prose. Then it was gone: the plan had nowhere to keep it, and the only trace was
a detail on the plan-creation audit entry.

The C1 gate needs it. When the deposit contradicts the plan's pipeline the gate
has to say which strategy refused it and which pipeline reads that strategy, and
re-deriving it means an ENA/GEO fetch on every page load, with an outage deciding
whether the gate renders.

Nullable with no backfill: NULL means "planned before this column existed", which
is honestly different from the None an extraction writes when no accession was
scoped or the deposit declared nothing usable.
"""

import sqlalchemy as sa
from alembic import op

revision = "127"
down_revision = "126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reproduction_plans", sa.Column("library_strategy", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("reproduction_plans", "library_strategy")
