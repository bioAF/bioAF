"""Backfill files.project_id from the linked experiment's project_id.

Files that were associated with an experiment before this fix had no project_id
set even though the experiment belonged to a project. This migration propagates
the experiment's project_id onto any such files.

Revision ID: 045
Revises: 044
Create Date: 2026-03-25
"""

from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE files
        SET project_id = e.project_id
        FROM experiments e
        WHERE files.experiment_id = e.id
          AND files.project_id IS NULL
          AND e.project_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Backfills are intentionally irreversible -- we cannot know which files
    # originally had project_id=NULL vs were legitimately updated.
    pass
