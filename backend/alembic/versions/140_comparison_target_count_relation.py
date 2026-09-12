"""change_7.5 section 3.1: a claim's count relation, as the paper states it.

"More than 5,000", "exactly 5,000" and "about 5,000" are three claims. A ComparisonTarget held only
the number, so the finding predicate could not tell them apart. ``comparison_targets.count_relation``
is one of ``= | > | >= | < | <= | approx``; null on a row written before it, which the predicate reads
as exactly the stated number, recorded as an assumption.

Revision ID: 140
Revises: 139
"""

import sqlalchemy as sa
from alembic import op

revision = "140"
down_revision = "139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comparison_targets", sa.Column("count_relation", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("comparison_targets", "count_relation")
