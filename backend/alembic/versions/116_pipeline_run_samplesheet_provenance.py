"""PipelineRun keeps the sheet it was given and the design that produced it.

Revision ID: 116
Revises: 115
Create Date: 2026-08-15

Additive and nullable. Runs that already exist keep a null, which honestly means
"this run predates the record" rather than "this run had no inputs". Back-filling
would be worse than the gap: the only way to produce a value for an old run is to
re-derive the sheet from today's samples, files and mapping, which is precisely
the thing this record exists to stop anyone doing.

Both are SNAPSHOTS. A mapping edited afterwards must not rewrite the history of a
run that already used it, so the run holds its own copy rather than a foreign key
to one that can change underneath it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "116"
down_revision = "115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("samplesheet_csv", sa.Text(), nullable=True))
    op.add_column(
        "pipeline_runs",
        sa.Column("samplesheet_mapping_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "samplesheet_mapping_json")
    op.drop_column("pipeline_runs", "samplesheet_csv")
