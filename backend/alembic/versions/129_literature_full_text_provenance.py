"""literature_papers: record where an automatically acquired PDF came from.

Revision ID: 129
Revises: 128
Create Date: 2026-10-31

An open-access copy fetched through OpenAlex may be the submitted, accepted or
published version, and the app has to say which. Nullable with no backfill:
existing PDFs were uploaded by hand, and NULL means "not acquired automatically".
"""

import sqlalchemy as sa
from alembic import op

revision = "129"
down_revision = "128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("literature_papers", sa.Column("full_text_version", sa.String(32), nullable=True))
    op.add_column("literature_papers", sa.Column("full_text_source", sa.String(32), nullable=True))
    op.add_column("literature_papers", sa.Column("full_text_source_url", sa.Text(), nullable=True))
    op.add_column("literature_papers", sa.Column("full_text_acquired_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("literature_papers", "full_text_acquired_at")
    op.drop_column("literature_papers", "full_text_source_url")
    op.drop_column("literature_papers", "full_text_source")
    op.drop_column("literature_papers", "full_text_version")
