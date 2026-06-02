"""Naming Profile redesign: template FK, drop closed-enum mapping columns.

Revision ID: 091
Revises: 090
Create Date: 2026-06-02

Drops the two closed-enum mapping columns (project_code_mappings,
experiment_code_mappings) that the original Naming Profile feature used to
resolve filename tokens to entity codes. Adds experiment_template_id, an
optional FK that lets the new wizard seed its field vocabulary from an
Experiment Template.

Marks all existing rows as 'deprecated'. The original feature was beta with
a single known user; per the redesign plan we do not migrate the segment
JSON to the new shape because the user will re-author profiles from scratch.

See local/Naming Profiles/redesign-plan.md and the proposed ADR in the same
directory for context.
"""

import sqlalchemy as sa
from alembic import op

revision = "091"
down_revision = "090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Mark every existing row deprecated. Their segments_json shape uses the
    # old closed-enum field names and is not parseable by the new parser.
    op.execute("UPDATE naming_profiles SET status = 'deprecated' WHERE status != 'deprecated'")

    # Add the optional template FK.
    op.add_column(
        "naming_profiles",
        sa.Column("experiment_template_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_naming_profiles_experiment_template",
        "naming_profiles",
        "experiment_templates",
        ["experiment_template_id"],
        ["id"],
    )

    # Drop the closed-enum mapping columns; the new design relies on
    # system-managed chips (Project / Experiment / Sample) for entity codes
    # rather than per-token mappings.
    op.drop_column("naming_profiles", "project_code_mappings")
    op.drop_column("naming_profiles", "experiment_code_mappings")


def downgrade() -> None:
    op.add_column(
        "naming_profiles",
        sa.Column(
            "experiment_code_mappings",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "naming_profiles",
        sa.Column(
            "project_code_mappings",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.drop_constraint(
        "fk_naming_profiles_experiment_template",
        "naming_profiles",
        type_="foreignkey",
    )
    op.drop_column("naming_profiles", "experiment_template_id")
    # Cannot recover the 'active' vs 'deprecated' distinction; leave statuses
    # as set on upgrade.
