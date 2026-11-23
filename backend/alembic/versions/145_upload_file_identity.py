"""Reserve each upload's File UUID and record what a filing copy observed.

Revision ID: 145
Revises: 144
Create Date: 2026-11-22

Organized raw objects are keyed by the File's permanent UUID, so two uploads with one display name
never share an object. An upload reserves that UUID before its first filing attempt (``file_uuid``,
unique, so two uploads cannot claim one identity) and records the received source's version and
size, and later the destination's version, before copying. ``cancel_from_stage`` records whether a
cancellation found the bytes already stored. Every column is nullable: existing
checkpoints and non-file purposes carry none, and nothing is backfilled.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "145"
down_revision = "144"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("upload_checkpoints", sa.Column("file_uuid", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("source_version", sa.String(255), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("source_observed_size", sa.BigInteger(), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("source_native_md5", sa.String(64), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("destination_version", sa.String(255), nullable=True))
    op.add_column("upload_checkpoints", sa.Column("cancel_from_stage", sa.String(24), nullable=True))
    op.create_index("uq_upload_checkpoints_file_uuid", "upload_checkpoints", ["file_uuid"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_upload_checkpoints_file_uuid", table_name="upload_checkpoints")
    for column in (
        "cancel_from_stage",
        "destination_version",
        "source_native_md5",
        "source_observed_size",
        "source_version",
        "file_uuid",
    ):
        op.drop_column("upload_checkpoints", column)
