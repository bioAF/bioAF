"""change_7.1 section 7: what a measurement is per.

Study 32 recorded Groff's sequencing depth as `mean_reads_per_cell` = 44,600,000 with the unit
"reads per library (mean)". The paper measures per LIBRARY, one library per embryo; bioAF's metric
is per CELL. Compared against a run, that claim is wrong by the number of cells in an embryo, and
the divergence would have been reported as the paper's.

The basis was recoverable from the claim's own unit string the whole time. It is a column now
because the comparison has to refuse on it, and a fact the comparison must branch on does not
belong in prose.

Revision ID: 135
Revises: 134
"""

import sqlalchemy as sa
from alembic import op

revision = "135"
down_revision = "134"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comparison_targets", sa.Column("measurement_basis", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("comparison_targets", "measurement_basis")
