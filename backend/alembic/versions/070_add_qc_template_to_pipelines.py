"""Add qc_template + qc_config_json to pipeline_catalog, custom_pipeline_versions, qc_dashboards.

Revision ID: 070
Revises: 069

Backs the per-pipeline QC dashboard config refactor. Each pipeline declares
which QC template it uses and may carry a render-config override; each
generated dashboard snapshots its resolved config so old runs still render
the way they were generated, even if the pipeline's config changes later.

Backfill (per spec):
- pipeline_catalog rows -> qc_template = 'scrnaseq' (matches current product
  reality; everything in the catalog today is scRNA-seq).
- custom_pipeline_versions rows -> qc_template = 'custom', qc_config_json
  stays NULL until the pipeline is next edited/run.
- qc_dashboards rows -> qc_config_json stays NULL; on next view the API
  substitutes the template's default config.

Additive only -- no drops in upgrade.
"""

import sqlalchemy as sa
from alembic import op

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_catalog",
        sa.Column("qc_template", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "pipeline_catalog",
        sa.Column("qc_config_json", sa.dialects.postgresql.JSONB(), nullable=True),
    )

    op.add_column(
        "custom_pipeline_versions",
        sa.Column("qc_template", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "custom_pipeline_versions",
        sa.Column("qc_config_json", sa.dialects.postgresql.JSONB(), nullable=True),
    )

    op.add_column(
        "qc_dashboards",
        sa.Column("qc_config_json", sa.dialects.postgresql.JSONB(), nullable=True),
    )

    op.execute("UPDATE pipeline_catalog SET qc_template = 'scrnaseq' WHERE qc_template IS NULL")
    op.execute("UPDATE custom_pipeline_versions SET qc_template = 'custom' WHERE qc_template IS NULL")


def downgrade() -> None:
    op.drop_column("qc_dashboards", "qc_config_json")
    op.drop_column("custom_pipeline_versions", "qc_config_json")
    op.drop_column("custom_pipeline_versions", "qc_template")
    op.drop_column("pipeline_catalog", "qc_config_json")
    op.drop_column("pipeline_catalog", "qc_template")
