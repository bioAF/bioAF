"""comparison_targets: record how a claim came to be measured against a metric.

Revision ID: 128
Revises: 127
Create Date: 2026-09-02

`metric_key` is the paper's own wording. Whether it reached a controlled metric was decided by an
alias table, and nothing recorded that a decision had been made at all: a claim keyed
`samd1_chip_peaks` resolved to nothing and the study reported "none of the paper's claimed metrics
could be compared", which is indistinguishable from what a genuinely unreproducible paper produces.

Binding is a model decision now (plan_6 step 2), so it is stored like one: what was chosen, why, how
confident the model was, and which model made the call.

Nullable with no backfill. NULL `bound_by` means "planned before this column existed", which is
honestly different from the `alias_table` a plan writes when the binding call could not run. Every
existing row keeps comparing through the alias table exactly as it does today.
"""

import sqlalchemy as sa
from alembic import op

revision = "128"
down_revision = "127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comparison_targets", sa.Column("bound_key", sa.String(length=100), nullable=True))
    op.add_column("comparison_targets", sa.Column("binding_reason", sa.Text(), nullable=True))
    op.add_column("comparison_targets", sa.Column("binding_confidence", sa.Float(), nullable=True))
    op.add_column("comparison_targets", sa.Column("bound_by_model", sa.String(length=255), nullable=True))
    op.add_column("comparison_targets", sa.Column("bound_by", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("comparison_targets", "bound_by")
    op.drop_column("comparison_targets", "bound_by_model")
    op.drop_column("comparison_targets", "binding_confidence")
    op.drop_column("comparison_targets", "binding_reason")
    op.drop_column("comparison_targets", "bound_key")
