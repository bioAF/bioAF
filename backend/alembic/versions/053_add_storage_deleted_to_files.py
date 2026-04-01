"""Add storage_deleted flag to files table.

When storage infrastructure is destroyed, files are marked as
storage_deleted rather than removed from the DB. This preserves
metadata (experiment associations, checksums, upload history)
while indicating the backing GCS object no longer exists.

Revision ID: 053
Revises: 052
"""

from alembic import op
import sqlalchemy as sa

revision = "053"
down_revision = "052"


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("storage_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index("idx_files_storage_deleted", "files", ["storage_deleted"])

    # If storage is not deployed, mark all existing files as storage_deleted.
    # This handles deployments where storage was destroyed before this
    # migration existed.
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM platform_config WHERE key = 'storage_deployed'")).fetchone()
    storage_deployed = row[0] if row else "false"
    if storage_deployed != "true":
        result = conn.execute(sa.text("UPDATE files SET storage_deleted = true WHERE storage_deleted = false"))
        if result.rowcount:
            print(f"  Marked {result.rowcount} file(s) as storage_deleted (storage not deployed)")


def downgrade() -> None:
    op.drop_index("idx_files_storage_deleted", table_name="files")
    op.drop_column("files", "storage_deleted")
