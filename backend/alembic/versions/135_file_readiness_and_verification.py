"""File readiness marker and persisted checksum verification records.

Revision ID: 135
Revises: 134
Create Date: 2026-11-15

- files.ready_at: when the file's bytes were first verified available. NULL means readiness has not
  been established under this contract, not that an older file is unusable. No default, no backfill.
- file_verifications: one current verification record per file, keeping the expected checksum (a
  caller or source assertion) separate from the observed one (read independently from storage), with
  durable work state (attempts, lease, progress, safe error code) that survives restarts.

No rows are created: legacy checksums stay unproven and no historical hashing is scheduled.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "135"
down_revision = "134"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("files", sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "file_verifications",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expectation_source", sa.String(32), nullable=True),
        sa.Column("expectation_revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("expected_algorithm", sa.String(16), nullable=True),
        sa.Column("expected_checksum", sa.String(64), nullable=True),
        sa.Column("discovery_context", JSONB, nullable=True),
        sa.Column("observed_checksum", sa.String(64), nullable=True),
        sa.Column("observed_method", sa.String(16), nullable=True),
        sa.Column("observed_bytes", sa.BigInteger, nullable=True),
        sa.Column("observed_uri", sa.String(1000), nullable=True),
        sa.Column("observed_object_version", sa.String(255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage", sa.String(32), nullable=True),
        sa.Column("bytes_processed", sa.BigInteger, nullable=True),
        sa.Column("total_bytes", sa.BigInteger, nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("retryable", sa.Boolean, nullable=True),
        sa.Column("artifact_uuid", UUID(as_uuid=True), nullable=True),
        sa.Column("artifact_uri", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("file_id", name="uq_file_verifications_file"),
    )
    op.create_index("ix_file_verifications_due", "file_verifications", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_file_verifications_due", table_name="file_verifications")
    op.drop_table("file_verifications")
    op.drop_column("files", "ready_at")
