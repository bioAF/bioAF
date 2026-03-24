"""Seed design_type controlled vocabulary values.

Pre-populates common experimental design classifications for the
provenance reporting system (ADR-037).

Revision ID: 042
Revises: 041
Create Date: 2026-03-24
"""

import sqlalchemy as sa
from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None

SEED_DATA = [
    ("design_type", "case-control", "Case-Control", 1, False),
    ("design_type", "cohort", "Cohort", 2, False),
    ("design_type", "time-series", "Time Series", 3, False),
    ("design_type", "dose-response", "Dose-Response", 4, False),
    ("design_type", "paired", "Paired", 5, False),
    ("design_type", "cross-sectional", "Cross-Sectional", 6, False),
    ("design_type", "longitudinal", "Longitudinal", 7, False),
    ("design_type", "factorial", "Factorial", 8, False),
    ("design_type", "other", "Other", 99, False),
]


def upgrade() -> None:
    table = sa.table(
        "controlled_vocabularies",
        sa.column("field_name", sa.String),
        sa.column("allowed_value", sa.String),
        sa.column("display_label", sa.String),
        sa.column("display_order", sa.Integer),
        sa.column("is_default", sa.Boolean),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        table,
        [
            {
                "field_name": row[0],
                "allowed_value": row[1],
                "display_label": row[2],
                "display_order": row[3],
                "is_default": row[4],
                "is_active": True,
            }
            for row in SEED_DATA
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM controlled_vocabularies WHERE field_name = 'design_type'")
