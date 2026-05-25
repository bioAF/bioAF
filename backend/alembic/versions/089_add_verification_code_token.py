"""Add a tokenized link column to verification_codes for password resets.

Revision ID: 089
Revises: 088
Create Date: 2026-05-25

Password-reset emails now carry a unique link (valid 60 minutes) in addition to
the 6-digit code. The link's secret is stored here so the reset page can be
validated and the reset confirmed by token rather than by email address.
"""

import sqlalchemy as sa
from alembic import op

revision = "089"
down_revision = "088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("verification_codes", sa.Column("token", sa.String(length=64), nullable=True))
    op.create_index("ix_verification_codes_token", "verification_codes", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_verification_codes_token", table_name="verification_codes")
    op.drop_column("verification_codes", "token")
