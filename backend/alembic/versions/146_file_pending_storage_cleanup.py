"""Record each File's one superseded object awaiting removal.

Revision ID: 146
Revises: 145
Create Date: 2026-11-22

A File moved to a new object keeps its old object until the new reference is committed; the old
object's pinned identity is recorded here in the same transaction, and removed by its owned cleanup
afterwards. Nullable, nothing backfilled; the partial index serves the periodic cleanup pass.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "146"
down_revision = "145"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("files", sa.Column("pending_storage_cleanup", postgresql.JSONB(), nullable=True))
    op.create_index(
        "ix_files_pending_storage_cleanup",
        "files",
        ["id"],
        postgresql_where=sa.text("pending_storage_cleanup IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_files_pending_storage_cleanup", table_name="files")
    op.drop_column("files", "pending_storage_cleanup")
