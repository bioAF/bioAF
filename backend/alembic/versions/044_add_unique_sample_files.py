"""Add unique constraint to sample_files(file_id, sample_id).

Revision ID: 044
Revises: 043
Create Date: 2026-03-25
"""

from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove any existing duplicates before adding the constraint
    op.execute(
        """
        DELETE FROM sample_files
        WHERE id NOT IN (
            SELECT MIN(id) FROM sample_files GROUP BY file_id, sample_id
        )
        """
    )
    op.create_unique_constraint("uq_sample_files_file_sample", "sample_files", ["file_id", "sample_id"])


def downgrade() -> None:
    op.drop_constraint("uq_sample_files_file_sample", "sample_files", type_="unique")
