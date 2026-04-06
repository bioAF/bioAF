"""Add setup code columns to organizations for terminal-based setup flow.

Revision ID: 061
Revises: 060

Stores a bcrypt-hashed setup code and its expiry timestamp so the CLI can
generate a one-time code that the web UI verifies.
"""

from alembic import op
import sqlalchemy as sa

revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("setup_code_hash", sa.String(255), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("setup_code_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "setup_code_expires_at")
    op.drop_column("organizations", "setup_code_hash")
