"""comparison_targets.unit: a paper's own words do not fit in 50 characters.

Revision ID: 126
Revises: 125
Create Date: 2026-08-30

Found re-planning 10.1038/s41598-023-33729-4 against the live demo. The extractor
faithfully captured a claim whose unit is "genes (NOTCH4, JAG1, LIFR, CCNA2,
CCND2, RB1, SMAD4, JUND, CREBBP)", 66 characters into a 50-character column. The
insert raised, the whole extraction rolled back, and the study could not be
planned at all: a 500 rather than anything a scientist could act on.

255 to match `source_locator`, which is the other free-text field on this table
and the one that has never overflowed. The service also clamps to the column
width now, because widening answers this paper and clamping answers the next one.
"""

import sqlalchemy as sa
from alembic import op

revision = "126"
down_revision = "125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "comparison_targets",
        "unit",
        existing_type=sa.String(50),
        type_=sa.String(255),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Lossy by nature: a value longer than 50 characters cannot survive the narrowing, so truncate
    # explicitly rather than letting the ALTER fail on whatever happens to be stored.
    op.execute("UPDATE comparison_targets SET unit = LEFT(unit, 50) WHERE LENGTH(unit) > 50")
    op.alter_column(
        "comparison_targets",
        "unit",
        existing_type=sa.String(255),
        type_=sa.String(50),
        existing_nullable=True,
    )
