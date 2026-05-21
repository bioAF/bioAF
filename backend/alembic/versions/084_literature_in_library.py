"""Add in_library flag and auto-accept Lit Review recommendations.

Revision ID: 084
Revises: 083
Create Date: 2026-05-19

Adds:
- literature_papers.in_library BOOLEAN NOT NULL DEFAULT true.

Data migration:
- New search-created papers should land outside the library by default. For
  existing rows that came from a search and have no active association, we
  set in_library = false. Papers from user_upload, lit_review_run, or that
  have any active association keep in_library = true.
- Existing recommendations that were left in 'pending' are converted to
  'accepted' so the Lit Review papers are coherent with the new
  auto-add-to-library behavior. Dismissed recommendations stay dismissed.
"""

import sqlalchemy as sa
from alembic import op

revision = "084"
down_revision = "083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "literature_papers",
        sa.Column(
            "in_library",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index(
        "ix_literature_papers_org_in_library",
        "literature_papers",
        ["organization_id", "in_library"],
    )

    op.execute(
        """
        UPDATE literature_papers p
        SET in_library = false
        WHERE p.provenance = 'source_search'
          AND NOT EXISTS (
            SELECT 1 FROM literature_associations a
            WHERE a.paper_id = p.id AND a.removed_at IS NULL
          )
          AND NOT EXISTS (
            SELECT 1 FROM literature_recommendations r
            WHERE r.paper_id = p.id AND r.status = 'accepted'
          )
        """
    )

    op.execute(
        """
        UPDATE literature_recommendations
        SET status = 'accepted',
            decided_by_user_id = literature_review_runs.triggered_by_user_id,
            decided_at = COALESCE(
                literature_recommendations.decided_at,
                literature_recommendations.created_at
            )
        FROM literature_review_runs
        WHERE literature_recommendations.review_run_id = literature_review_runs.id
          AND literature_recommendations.status = 'pending'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_literature_papers_org_in_library", table_name="literature_papers")
    op.drop_column("literature_papers", "in_library")
