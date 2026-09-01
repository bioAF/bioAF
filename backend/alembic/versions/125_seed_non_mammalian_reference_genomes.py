"""Seed the reference_genome vocabulary past human and mouse.

Revision ID: 125
Revises: 124
Create Date: 2026-08-30

bioAF could resolve four assemblies (GRCh38, GRCh37, GRCm39, GRCm38). Anything
else normalized to nothing, the launch took the pipeline's seeded default, and a
non-human study aligned against the wrong genome and completed green. That capped
every non-human, non-mouse paper at Layer 0 in lit_validation, which is most of
the plant, model-organism and agricultural literature.

Widening the code tables alone would not have worked. `launch_run` validates
`reference_genome` against this vocabulary and 422s on anything absent, so an
assembly bioAF could resolve but this table did not list would refuse the very
studies it was added for.

Each value is inactive by default, matching how 011 seeded everything but GRCh38:
one default, the rest available. Insert-if-absent rather than a bulk insert,
because 011 is create-once and an instance that has since edited its vocabulary by
hand must not collide here.
"""

import sqlalchemy as sa
from alembic import op

revision = "125"
down_revision = "124"
branch_labels = None
depends_on = None

# (field_name, allowed_value, display_label, display_order, is_default)
REFERENCE_GENOMES = [
    ("reference_genome", "GRCz11", "Zebrafish (GRCz11)", 7, False),
    ("reference_genome", "mRatBN7.2", "Rat (mRatBN7.2)", 8, False),
    ("reference_genome", "WBcel235", "C. elegans (WBcel235)", 9, False),
    ("reference_genome", "BDGP6", "Drosophila (BDGP6)", 10, False),
    ("reference_genome", "TAIR10", "Arabidopsis (TAIR10)", 11, False),
]


def upgrade() -> None:
    for field_name, allowed_value, display_label, display_order, is_default in REFERENCE_GENOMES:
        op.execute(
            sa.text(
                """
                INSERT INTO controlled_vocabularies
                    (field_name, allowed_value, display_label, display_order, is_default, is_active)
                SELECT :field_name, :allowed_value, :display_label, :display_order, :is_default, TRUE
                WHERE NOT EXISTS (
                    SELECT 1 FROM controlled_vocabularies
                    WHERE field_name = :field_name AND allowed_value = :allowed_value
                )
                """
            ).bindparams(
                field_name=field_name,
                allowed_value=allowed_value,
                display_label=display_label,
                display_order=display_order,
                is_default=is_default,
            )
        )


def downgrade() -> None:
    for _field_name, allowed_value, _label, _order, _default in REFERENCE_GENOMES:
        op.execute(
            sa.text(
                "DELETE FROM controlled_vocabularies "
                "WHERE field_name = 'reference_genome' AND allowed_value = :allowed_value"
            ).bindparams(allowed_value=allowed_value)
        )
