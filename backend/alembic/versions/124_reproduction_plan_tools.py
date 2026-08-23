"""reproduction_plans.tools_json: keep the tools the paper said it used.

Revision ID: 124
Revises: 123
Create Date: 2026-08-22

The extractor already asks the model for `method.tools` and fills it from the
paper's methods section. That list reached `map_method`, which spent it on ONE
boolean (does the paper mention nf-core) and a prose sentence in mapping_notes,
and then it was gone: the plan had nowhere to keep it.

It is the only input an honest divergence attribution has. bioAF runs STARsolo;
most published scRNA-seq papers used CellRanger; the two cell-callers routinely
disagree on cell count by more than the 25% tolerance. Knowing which tool the
paper used is what turns that from an unexplained divergence that silently vetoes
a reproduced finding into a named, expected difference between two tools.

Nullable with no backfill: NULL means "extracted before this column existed",
which is honestly different from the [] an extraction writes when a paper names
no tools, and the attribution treats both as "nothing to attribute with".
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "124"
down_revision = "123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reproduction_plans", sa.Column("tools_json", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("reproduction_plans", "tools_json")
