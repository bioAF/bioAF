"""Attach naming_profile_id to experiment_templates and experiments.

Revision ID: 092
Revises: 091
Create Date: 2026-06-02

A team can now set a default naming profile on an experiment template; that
default is inherited by experiments created from the template and can be
overridden per-experiment. Both columns are nullable: a profile is purely
optional. No data backfill is required; everything starts NULL.

Follow-up auto-ingest rework will use these columns to decide which profile
to parse an incoming file against (see ADR-058).
"""

import sqlalchemy as sa
from alembic import op

revision = "092"
down_revision = "091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiment_templates",
        sa.Column("naming_profile_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_experiment_templates_naming_profile",
        "experiment_templates",
        "naming_profiles",
        ["naming_profile_id"],
        ["id"],
    )

    op.add_column(
        "experiments",
        sa.Column("naming_profile_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_experiments_naming_profile",
        "experiments",
        "naming_profiles",
        ["naming_profile_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_experiments_naming_profile",
        "experiments",
        type_="foreignkey",
    )
    op.drop_column("experiments", "naming_profile_id")

    op.drop_constraint(
        "fk_experiment_templates_naming_profile",
        "experiment_templates",
        type_="foreignkey",
    )
    op.drop_column("experiment_templates", "naming_profile_id")
