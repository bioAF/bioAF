"""Rename gcs_uri -> storage_uri (BAL rework, Phase 4).

The opaque object-store URI column was named gcs_uri, which presumes a GCS
backend. Rename it to the backend-neutral storage_uri on every table that has
it. The ORM keeps a ``gcs_uri`` synonym so existing callers and API responses
continue to resolve during the transition; this migration only renames the
physical column. Reversible: downgrade renames back.

Revision ID: 102
Revises: 101
Create Date: 2026-06-07
"""

from alembic import op

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None

_TABLES = ("files", "lab_documents", "lab_document_versions", "reference_dataset_files")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, "gcs_uri", new_column_name="storage_uri")


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, "storage_uri", new_column_name="gcs_uri")
