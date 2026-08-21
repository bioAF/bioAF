"""Sample.sex (optional donor sex).

Revision ID: 114
Revises: 113
Create Date: 2026-08-11

Additive and nullable. nf-core/raredisease requires a per-sample `sex` column,
and bioAF had nowhere to put it: organism, tissue_type and donor_source already
live on the sample and already feed the samplesheet, but sex did not exist.

Deliberately OPTIONAL. Most assays neither use nor need it, so it must never
become a required field on sample intake. A pipeline that requires it still
blocks with a clear message naming the field when it is empty, which is the same
behavior as any other unsourceable required column.
"""

import sqlalchemy as sa
from alembic import op

revision = "114"
down_revision = "113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("samples", sa.Column("sex", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("samples", "sex")
