"""change_7.1 section 3: a claim's context, stored as structure rather than prose.

A comparison target carried a metric name, a value, a unit and a locator. That is not enough to
know what was measured. Groff et al. reports 54 samples in its inventory and analyses 51 after QC;
it reports 194 significant genes of which 88 clear a fold-change cutoff; and its six up-regulated
genes are up RELATIVE TO good-quality embryos, so a poor-versus-good contrast reverses every sign.
All three distinctions lived in the prose the extractor threw away, and the comparison engine then
checked the paper's numbers against a different population, a different threshold and, on one
contrast, the opposite direction.

``metric_key`` becomes nullable so a claim nothing measures is PRESERVED rather than dropped.
Groff's digital-karyotype and TE-WE concordance claims are central to the paper and vanished
silently, which made the assessment look complete when two of its findings had never been read.

Revision ID: 134
Revises: 133
"""

import sqlalchemy as sa
from alembic import op

revision = "134"
down_revision = "133"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Typed columns rather than a JSON blob: the comparison engine BRANCHES on direction and
    # threshold, and a mistyped key in a blob reads as "no context" and silently runs the
    # comparison undirected. Every other field on this table is a column for the same reason.
    op.add_column("comparison_targets", sa.Column("claim_text", sa.Text(), nullable=True))
    op.add_column("comparison_targets", sa.Column("sample_subset", sa.String(length=255), nullable=True))
    op.add_column("comparison_targets", sa.Column("qc_stage", sa.String(length=100), nullable=True))
    op.add_column("comparison_targets", sa.Column("direction", sa.String(length=20), nullable=True))
    op.add_column("comparison_targets", sa.Column("threshold", sa.Float(), nullable=True))
    op.add_column("comparison_targets", sa.Column("threshold_kind", sa.String(length=50), nullable=True))
    op.add_column("comparison_targets", sa.Column("output_type", sa.String(length=50), nullable=True))

    # A claim no controlled metric measures is still one of the paper's claims. Dropping it made
    # the report silent about the findings it could not check.
    op.alter_column("comparison_targets", "metric_key", existing_type=sa.String(length=100), nullable=True)


def downgrade() -> None:
    # Rows whose metric_key is NULL exist only because this migration allowed them. They cannot be
    # made NOT NULL again without inventing a metric name, so they go.
    op.execute("DELETE FROM comparison_targets WHERE metric_key IS NULL")
    op.alter_column("comparison_targets", "metric_key", existing_type=sa.String(length=100), nullable=False)
    for column in (
        "output_type",
        "threshold_kind",
        "threshold",
        "direction",
        "qc_stage",
        "sample_subset",
        "claim_text",
    ):
        op.drop_column("comparison_targets", column)
