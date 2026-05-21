"""Add organizations.lit_review_relevance_threshold.

Revision ID: 085
Revises: 084
Create Date: 2026-05-19

Adds a per-org default minimum relevance score for AI Lit Review Runs.
When a Lit Review Run is launched without an explicit score_threshold, this
value is used as the cutoff. Default 0.65 chosen so that only papers the LLM
scores as solidly relevant land in the Library.
"""

import sqlalchemy as sa
from alembic import op

revision = "085"
down_revision = "084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "lit_review_relevance_threshold",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.65"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "lit_review_relevance_threshold")
