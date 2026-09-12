"""change_7.5 stage 2: reported experiments, typed resources, per-claim checks and the selection.

Study 38 held one paper-level method for a ChIP-seq and an RNA-seq experiment, chose the ChIP-seq
workflow before any claim, and refused every RNA-seq claim. These columns carry what claim-first
planning needs as structure:

- ``reproduction_plans.reported_experiments_json``: each experiment the paper reports, with its own
  assay, conditions, reference (assembly and annotation) and linked resources.
- ``reproduction_plans.resources_json``: every resource the paper names, typed, with bioAF's ability
  to retrieve and analyze it kept separate from its discovery.
- ``reproduction_plans.analysis_selection_json``: the selected claim and check, and the workflow,
  reference, input and mapping chosen from them, as revisions.
- ``comparison_targets.reported_experiment_id``: the experiment a claim was measured in.
- ``comparison_targets.aggregation`` and ``binding_facts``: population, aggregation and denominator as
  the binding read them, with quotes.
- ``comparison_targets.checks``: the claim's four checks with status and reason.

Revision ID: 139
Revises: 138
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "139"
down_revision = "138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reproduction_plans", sa.Column("reported_experiments_json", JSONB(), nullable=True))
    op.add_column("reproduction_plans", sa.Column("resources_json", JSONB(), nullable=True))
    op.add_column("reproduction_plans", sa.Column("analysis_selection_json", JSONB(), nullable=True))
    op.add_column("comparison_targets", sa.Column("reported_experiment_id", sa.String(32), nullable=True))
    op.add_column("comparison_targets", sa.Column("aggregation", sa.String(32), nullable=True))
    op.add_column("comparison_targets", sa.Column("binding_facts", JSONB(), nullable=True))
    op.add_column("comparison_targets", sa.Column("checks", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("comparison_targets", "checks")
    op.drop_column("comparison_targets", "binding_facts")
    op.drop_column("comparison_targets", "aggregation")
    op.drop_column("comparison_targets", "reported_experiment_id")
    op.drop_column("reproduction_plans", "analysis_selection_json")
    op.drop_column("reproduction_plans", "resources_json")
    op.drop_column("reproduction_plans", "reported_experiments_json")
