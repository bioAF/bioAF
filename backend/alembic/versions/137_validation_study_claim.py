"""change_7.2 section 2: a study is owned before it is read.

Two writers ran `read_and_plan` on the same study and produced two full extractions and two plans.
There is no lease, claim or row lock anywhere in this application today, so the mechanism is new:
a fencing token, its holder, and when the claim expires.

**Its own table, deliberately.** A claim on the study row would be taken and renewed on the same row
the owned work is writing to, so a renewal on a second connection would block behind the work's own
uncommitted transaction and the claim would expire under the worker it was protecting. The claim
also must not stamp `updated_at` on the study every thirty seconds.

The token is the fence. Every write performed under a claim carries it, and a write whose claim has
expired or been superseded is rejected rather than applied, so an expired worker that finishes late
cannot land its result over the worker that replaced it.

Revision ID: 137
Revises: 136
"""

import sqlalchemy as sa
from alembic import op

revision = "137"
down_revision = "136"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_study_claims",
        sa.Column(
            "validation_study_id",
            sa.Integer(),
            sa.ForeignKey("validation_studies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("holder", sa.String(length=64), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("validation_study_claims")
