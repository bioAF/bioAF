"""Create experiment_field_defaults table.

Revision ID: 030
Revises: 029
Create Date: 2026-03-15

Stores experiment-level default values and requirement overrides
for MINSEQ and sample metadata fields. Values propagate as defaults
when creating new samples under the experiment.
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_field_defaults",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment_id", sa.Integer(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("default_value", sa.Text(), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "field_name", name="uq_experiment_field_defaults_exp_field"),
    )
    op.create_index("ix_experiment_field_defaults_experiment_id", "experiment_field_defaults", ["experiment_id"])


def downgrade() -> None:
    op.drop_index("ix_experiment_field_defaults_experiment_id", table_name="experiment_field_defaults")
    op.drop_table("experiment_field_defaults")
