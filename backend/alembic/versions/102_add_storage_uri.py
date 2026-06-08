"""Add backend-neutral storage_uri columns (BAL rework, Phase 4, expand phase).

Expand/contract rename of gcs_uri -> storage_uri, done safely for already-live
installs. This is the EXPAND step: add a nullable storage_uri column to every
table that has gcs_uri and backfill it from gcs_uri. gcs_uri stays in place
(still NOT NULL) and the ORM keeps the two columns in sync on every write
(app.models._storage_uri_sync) so old/external readers of gcs_uri keep working.

A later CONTRACT migration drops gcs_uri once all readers use storage_uri; that
is the only breaking step and is scheduled deliberately. This migration is
purely additive and reversible.

Revision ID: 102
Revises: 101
Create Date: 2026-06-07
"""

import sqlalchemy as sa
from alembic import op

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None

# (table, column type) for every table carrying the opaque object-store URI.
_TABLES = (
    ("files", sa.String(length=1000)),
    ("lab_documents", sa.String(length=1000)),
    ("lab_document_versions", sa.String(length=1000)),
    ("reference_dataset_files", sa.Text()),
)


def upgrade() -> None:
    for table, coltype in _TABLES:
        op.add_column(table, sa.Column("storage_uri", coltype, nullable=True))
        op.execute(f"UPDATE {table} SET storage_uri = gcs_uri")  # noqa: S608 (static table names)


def downgrade() -> None:
    for table, _coltype in _TABLES:
        op.drop_column(table, "storage_uri")
