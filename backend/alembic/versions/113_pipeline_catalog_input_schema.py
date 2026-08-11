"""PipelineCatalogEntry.input_schema_json (nf-core samplesheet contract).

Revision ID: 113
Revises: 112
Create Date: 2026-08-11

Additive only. Stores the pipeline's own ``assets/schema_input.json``, fetched at
install alongside the existing ``nextflow_schema.json`` and pinned to the same
version, so samplesheet generation can be keyed on the pipeline's published
contract rather than on a substring of its name.

Nullable, and null is meaningful: it means "not fetched yet", which the launch
path resolves lazily and persists. No backfill is needed, and an entry installed
before this revision keeps working exactly as it does today until its first
launch. A pipeline that ships no such file stores an explicit absent marker so
the lazy path does not re-request a known 404 on every launch.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "113"
down_revision = "112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_catalog",
        sa.Column("input_schema_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_catalog", "input_schema_json")
