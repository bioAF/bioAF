"""Provider-cancellable upload sessions and writer settlement.

Revision ID: 142
Revises: 141
Create Date: 2026-11-21

Direct uploads now use a provider upload session (a GCS resumable session or an S3 multipart
upload) that bioAF can inspect and cancel. A checkpoint records the session, its progress, a
cancellation that is waiting for the provider to confirm it, and an administrator's audited
release of a writer whose outcome could not be confirmed. Existing rows keep their meaning:
``signed`` rows are the presigned URLs issued before this release.
"""

import sqlalchemy as sa
from alembic import op

revision = "142"
down_revision = "141"
branch_labels = None
depends_on = None

_OLD_STAGES = "stage IN ('issued', 'transferring', 'received', 'filed', 'cancelled', 'failed')"
_NEW_STAGES = "stage IN ('issued', 'transferring', 'received', 'filed', 'cancelling', 'cancelled', 'failed')"


def upgrade() -> None:
    op.add_column(
        "upload_checkpoints", sa.Column("purpose", sa.String(24), nullable=False, server_default=sa.text("'file'"))
    )
    op.add_column("upload_checkpoints", sa.Column("session_protocol", sa.String(24), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("session_handle", sa.Text, nullable=True))
    op.add_column("upload_checkpoints", sa.Column("progress_bytes", sa.BigInteger, nullable=True))
    op.add_column("upload_checkpoints", sa.Column("progress_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("assembly_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "upload_checkpoints",
        sa.Column(
            "cancel_requested_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
    op.add_column(
        "upload_checkpoints",
        sa.Column("reconcile_attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.add_column("upload_checkpoints", sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "upload_checkpoints",
        sa.Column("released_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("upload_checkpoints", sa.Column("release_reason", sa.Text, nullable=True))
    op.drop_constraint("ck_upload_checkpoints_stage", "upload_checkpoints", type_="check")
    op.create_check_constraint("ck_upload_checkpoints_stage", "upload_checkpoints", _NEW_STAGES)
    op.drop_constraint("ck_upload_checkpoints_kind", "upload_checkpoints", type_="check")
    op.create_check_constraint(
        "ck_upload_checkpoints_kind", "upload_checkpoints", "kind IN ('signed', 'proxied', 'session')"
    )
    op.create_check_constraint(
        "ck_upload_checkpoints_purpose", "upload_checkpoints", "purpose IN ('file', 'lab_document', 'reference')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_upload_checkpoints_purpose", "upload_checkpoints", type_="check")
    op.execute("UPDATE upload_checkpoints SET stage = 'cancelled' WHERE stage = 'cancelling'")
    op.execute("UPDATE upload_checkpoints SET kind = 'signed' WHERE kind = 'session'")
    op.drop_constraint("ck_upload_checkpoints_kind", "upload_checkpoints", type_="check")
    op.create_check_constraint("ck_upload_checkpoints_kind", "upload_checkpoints", "kind IN ('signed', 'proxied')")
    op.drop_constraint("ck_upload_checkpoints_stage", "upload_checkpoints", type_="check")
    op.create_check_constraint("ck_upload_checkpoints_stage", "upload_checkpoints", _OLD_STAGES)
    for column in (
        "release_reason",
        "released_by_user_id",
        "released_at",
        "next_reconcile_at",
        "reconcile_attempts",
        "cancel_requested_by_user_id",
        "cancel_requested_at",
        "assembly_requested_at",
        "progress_at",
        "progress_bytes",
        "session_handle",
        "session_protocol",
        "purpose",
    ):
        op.drop_column("upload_checkpoints", column)
