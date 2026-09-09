"""change_7.2 section 2: a study is owned before it is read.

Two writers ran `read_and_plan` on the same study and produced two full extractions and two plans.
There is no lease, claim or row lock anywhere in this application today, so the mechanism is new:
a fencing token, its holder, and when the claim expires.

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
    op.add_column("validation_studies", sa.Column("claim_token", sa.String(length=64), nullable=True))
    op.add_column("validation_studies", sa.Column("claim_holder", sa.String(length=64), nullable=True))
    op.add_column("validation_studies", sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("validation_studies", "claim_expires_at")
    op.drop_column("validation_studies", "claim_holder")
    op.drop_column("validation_studies", "claim_token")
