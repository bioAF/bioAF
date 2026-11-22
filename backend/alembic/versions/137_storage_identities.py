"""Storage identity evidence and store health.

Revision ID: 137
Revises: 136
Create Date: 2026-11-21

Adds two empty tables. ``storage_identities`` records what bioAF has established about each storage
bucket (discovered from agreeing provider evidence and recorded deployment, or enrolled by an
administrator). ``storage_store_checks`` keeps the latest read-only inspection per store. No identity
is inferred here and no cloud call is made: existing installations start with no records and are
inspected by the running application.
"""

import sqlalchemy as sa
from alembic import op

revision = "137"
down_revision = "136"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_identities",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("store", sa.String(32), nullable=False),
        sa.Column("bucket_name", sa.String(255), nullable=False),
        sa.Column("storage_uid", sa.String(32), nullable=True),
        sa.Column("owner", sa.String(64), nullable=True),
        sa.Column("provider_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("marker", sa.String(64), nullable=True),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("recorded_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("basis IN ('discovered', 'enrolled')", name="ck_storage_identities_basis"),
    )
    op.create_index(
        "uq_storage_identities_active_bucket",
        "storage_identities",
        ["bucket_name"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "storage_store_checks",
        sa.Column("store", sa.String(32), primary_key=True),
        sa.Column("bucket_name", sa.String(255), nullable=False),
        sa.Column("health", sa.String(32), nullable=False),
        sa.Column("basis", sa.String(16), nullable=True),
        sa.Column("observed_owner", sa.String(64), nullable=True),
        sa.Column("observed_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("storage_store_checks")
    op.drop_index("uq_storage_identities_active_bucket", table_name="storage_identities")
    op.drop_table("storage_identities")
