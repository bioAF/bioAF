"""samplesheet_mappings.columns_json: the columns a scientist declared.

Revision ID: 121
Revises: 120
Create Date: 2026-08-19

Seventeen pipelines in the catalog publish no `schema_input.json`. For those,
bioAF emitted a fixed `sample,fastq_1,fastq_2` header and ignored everything a
scientist stated: the same request with and without values produced a
byte-identical sheet, because `generate_generic_sheet(samples, parameters)` took
neither a mapping nor stated values. Nothing they could say reached the file.

Decision 1 of 2026-08-19: they declare the columns themselves, in the same
`{"fields": [{name, type, required}]}` shape the experiment field editor already
uses, each carrying a BINDING that says where its value comes from. This is where
that declaration lives.

On the MAPPING rather than on the pipeline, which is the correction design-02
section 4 already made for bindings and values: the right columns depend on the
experiment, so a per-pipeline declaration would propagate a shape that is correct
once and silently wrong afterwards. It inherits the same ladder (experiment,
promotable to project, promotable to organisation) and the same "most specific
scope wins" resolution, so no new scope concept is introduced.

Nullable, with no backfill and no default. NULL means "nothing declared", which
every reader already treats as "we do not know" and answers with today's generic
sheet. A saved declaration is the only thing that changes what is emitted, so
this migration alone cannot alter any existing launch.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "121"
down_revision = "120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "samplesheet_mappings",
        sa.Column("columns_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("samplesheet_mappings", "columns_json")
