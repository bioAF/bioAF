"""PipelineCatalogEntry.input_schema_version (which release the contract is for).

Revision ID: 117
Revises: 116
Create Date: 2026-08-15

Additive and nullable. A samplesheet contract was fetched once at install and
pinned to the tag current then, and nothing recorded WHICH tag, so an upgraded
pipeline went on being validated against its old rules: still requiring a column
that had been dropped, still blind to one that had been added.

NULL means the contract predates this column, and is read as "assume current"
rather than as a mismatch. The alternative would re-fetch every installed
pipeline on its next launch, putting the whole catalog's worth of network calls
onto launch paths, to correct a drift that may not exist.
"""

import sqlalchemy as sa
from alembic import op

revision = "117"
down_revision = "116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_catalog", sa.Column("input_schema_version", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_catalog", "input_schema_version")
