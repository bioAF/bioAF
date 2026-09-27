"""plan_8_7 stage 2: how far the paper's ASSESSMENT has got, apart from what its execution is doing.

`validation_studies.state` conflates the two. `classified` is the end of the execution lifecycle, and
nothing else could say whether the scientific questions had been asked, so a study whose data could
not be acquired and a study whose evidence had been reviewed in full looked the same, and a reader
could not tell a report that was still being built from one that was finished.

Two nullable columns, so every historical row reads as NULL and its report is projected from `state`
exactly as it always was: nothing is rescored and no old report changes.

Revision ID: 145
Revises: 144
"""

import sqlalchemy as sa
from alembic import op

revision = "145"
down_revision = "144"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("validation_studies", sa.Column("assessment_state", sa.String(20), nullable=True))
    op.add_column("validation_studies", sa.Column("assessment_revision", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("validation_studies", "assessment_revision")
    op.drop_column("validation_studies", "assessment_state")
