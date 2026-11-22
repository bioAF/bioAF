"""Durable upload checkpoints.

Revision ID: 139
Revises: 138
Create Date: 2026-11-21

Adds ``upload_checkpoints``, written before a signed URL is issued or a proxied transfer starts and
kept until the bytes are filed, so an upload survives a restart and a failed filing step is
recoverable. Uploads pending in memory before this release are not carried over (they were lost on
any restart before, too); no data is written here.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "139"
down_revision = "138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("source_uri", sa.Text, nullable=False),
        sa.Column("planned_destination", sa.Text, nullable=True),
        sa.Column("received_size", sa.BigInteger, nullable=True),
        sa.Column("expected_size", sa.BigInteger, nullable=True),
        sa.Column("expected_md5", sa.String(64), nullable=True),
        sa.Column("asserted_md5", sa.String(64), nullable=True),
        sa.Column("project_id", sa.Integer, nullable=True),
        sa.Column("experiment_id", sa.Integer, nullable=True),
        sa.Column("sample_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_global", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("reference", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(48), nullable=True),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("grant_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "stage IN ('issued', 'transferring', 'received', 'filed', 'cancelled', 'failed')",
            name="ck_upload_checkpoints_stage",
        ),
        sa.CheckConstraint("kind IN ('signed', 'proxied')", name="ck_upload_checkpoints_kind"),
    )
    op.create_index("ix_upload_checkpoints_org_stage", "upload_checkpoints", ["organization_id", "stage"])


def downgrade() -> None:
    op.drop_index("ix_upload_checkpoints_org_stage", table_name="upload_checkpoints")
    op.drop_table("upload_checkpoints")
