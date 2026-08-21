"""The run's record of what it emitted: the sheet with UIDs, and the map.

Revision ID: 122
Revises: 121
Create Date: 2026-08-19

Decision 3 of 2026-08-19, in the owner's words: "The sheet as it is right now,
but also add a column with the UIDs. This will mainly just be metadata, but does
allow for manual verification that files aren't misattributed if someone is
REALLY anal about it."

The constraint that makes it safe, and which must not be lost: **the UID column
never reaches the CSV submitted to Nextflow.** nf-schema validates the whole
sheet against the pipeline's declared properties, so one undeclared column fails
all of it and would break every launch. `samplesheet_csv` therefore stays exactly
what was handed over, and this column holds the annotated copy.

That is the human-readable half. The half bioAF processes on is
`samplesheet_emitted_json`: what each emitted NAME stood for. Both are built from
one computation, so they cannot disagree about what ran.

Kept apart from `samplesheet_mapping_json`, which answers a different question.
That column is the DESIGN, with the stamps saying who stated each value; this one
is what the run PUT IN THE SHEET. Folding them together would make "the design"
mean two things.

Nullable, no backfill. A run launched before this has no record of what it
emitted and nothing is reconstructed for it: re-deriving would read today's
samples, files and mapping, none of which are what that run received. Those runs
keep matching outputs by the sample's current name, exactly as they do now.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "122"
down_revision = "121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("samplesheet_snapshot_csv", sa.Text(), nullable=True))
    op.add_column(
        "pipeline_runs",
        sa.Column("samplesheet_emitted_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "samplesheet_emitted_json")
    op.drop_column("pipeline_runs", "samplesheet_snapshot_csv")
