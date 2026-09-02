"""organizations.lit_validation_autonomy: how much of validation the model decides.

Revision ID: 129
Revises: 128
Create Date: 2026-09-02

Two values, "assisted" and "autonomous". Assisted is the default and is today's behaviour: the model
proposes and anything it declined is surfaced at the C1 gate for a person. Autonomous asks the model
to choose and records how sure it was.

NOT NULL with a server default, unlike migration 128's nullable columns, because there is no honest
"unset" for this one: every org is in one mode or the other, and an org that never touched the
setting is in the mode the product ships with.

The C1 gate is human in both modes. This setting does not move it.
"""

import sqlalchemy as sa
from alembic import op

revision = "129"
down_revision = "128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("lit_validation_autonomy", sa.String(length=16), nullable=False, server_default="assisted"),
    )


def downgrade() -> None:
    op.drop_column("organizations", "lit_validation_autonomy")
