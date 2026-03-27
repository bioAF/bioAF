"""Notebook output provenance: add accessed_at, unique constraint, source FK.

Revision ID: 047
Revises: 046
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add accessed_at column to notebook_session_files if it does not exist
    op.add_column(
        "notebook_session_files",
        sa.Column("accessed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Deduplicate existing rows before adding unique constraint
    op.execute(
        """
        DELETE FROM notebook_session_files
        WHERE id NOT IN (
            SELECT MIN(id) FROM notebook_session_files GROUP BY session_id, file_id, access_type
        )
        """
    )
    op.create_unique_constraint(
        "uq_notebook_session_files_session_file_access",
        "notebook_session_files",
        ["session_id", "file_id", "access_type"],
    )

    # Add source_notebook_session_id FK to files
    op.add_column(
        "files",
        sa.Column("source_notebook_session_id", sa.Integer(), sa.ForeignKey("compute_sessions.id"), nullable=True),
    )
    op.create_index("idx_files_source_notebook_session_id", "files", ["source_notebook_session_id"])


def downgrade() -> None:
    op.drop_index("idx_files_source_notebook_session_id", "files")
    op.drop_column("files", "source_notebook_session_id")
    op.drop_constraint("uq_notebook_session_files_session_file_access", "notebook_session_files", type_="unique")
    op.drop_column("notebook_session_files", "accessed_at")
