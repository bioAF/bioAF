"""intended_route: the route chosen at the button, before the paper is read.

Revision ID: 133
Revises: 132
Create Date: 2026-09-08

Clicking "Validate findings" used to create a study in `requested` and land the user on a page that
read "Requested - Step 1 of 9" with an in-progress badge, while TWO manual clicks ("Read paper",
then "Approve" with the route modal) stood between it and any work. Both stops looked like progress,
so a study could sit untouched while the page implied it was running.

The route is now chosen once, at the button, and recorded here. The driver reads the paper and then
approves onto this route by itself. `evidence_json["route"]` still records what a study ACTUALLY ran,
which is a different fact: this column is the intent, set before anything is known about the paper,
and the two can disagree when the choice turns out to be impossible.

Nullable, and null is meaningful: a study created without one keeps the manual C1 gate, so every
other entry point behaves exactly as it did. No backfill for that reason.
"""

import sqlalchemy as sa
from alembic import op

revision = "133"
down_revision = "132"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "validation_studies",
        sa.Column("intended_route", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("validation_studies", "intended_route")
