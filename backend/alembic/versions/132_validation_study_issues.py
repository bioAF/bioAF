"""validation_study_issues: steps that hit an error while validating a paper.

Revision ID: 132
Revises: 131
Create Date: 2026-09-07

plan_7 step 14a/14c. A refusal was undetectable at every layer: no provider client read the field
that says a model declined, `ProviderError.error_class` had no `refusal` value, and the detail died
in a `logger.warning`. A model refusing every claim binding produced a plan that had silently fallen
back to the alias table, with nothing on screen to say so.

Study-scoped rather than plan-scoped, because a refusal can happen before a plan exists. A table
rather than a key on `evidence_json`, because the request path and the background driver both append
to it and the driver rewrites that bundle wholesale on nearly every tick.

No backfill: every study predating this had its issues logged and discarded, and inventing rows for
them would assert something we do not know.
"""

import sqlalchemy as sa
from alembic import op

revision = "132"
down_revision = "131"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_study_issues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("validation_study_id", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(length=200), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("impact", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["validation_study_id"], ["validation_studies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_validation_study_issues_validation_study_id",
        "validation_study_issues",
        ["validation_study_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_validation_study_issues_validation_study_id", table_name="validation_study_issues")
    op.drop_table("validation_study_issues")
