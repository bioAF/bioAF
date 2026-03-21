"""Backfill source_type and source_pipeline_run_id for existing files.

Revision ID: 034
Revises: 033
Create Date: 2026-03-20

Uses qc_dashboards.plots_json, plot_archive, and GCS URI patterns to
classify files that were incorrectly defaulted to 'upload'.
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Files referenced by qc_dashboards.plots_json -> source_type='qc_dashboard'
    # plots_json is a JSONB array of objects like [{"file_id": 123, ...}, ...]
    conn.execute(
        sa.text("""
            UPDATE files
            SET source_type = 'qc_dashboard',
                source_pipeline_run_id = qd.pipeline_run_id
            FROM qc_dashboards qd,
                 jsonb_array_elements(qd.plots_json) AS plot
            WHERE files.id = (plot->>'file_id')::int
              AND files.source_type = 'upload'
              AND files.uploader_user_id IS NULL
        """)
    )

    # 2. Files referenced by plot_archive -> source_type='plot_archive'
    conn.execute(
        sa.text("""
            UPDATE files
            SET source_type = 'plot_archive',
                source_pipeline_run_id = pa.pipeline_run_id
            FROM plot_archive pa
            WHERE files.id = pa.file_id
              AND files.source_type = 'upload'
              AND files.uploader_user_id IS NULL
        """)
    )

    # 3. Remaining image/pdf files with no uploader and a results-bucket GCS URI
    # Parse pipeline_run_id from path: .../pipeline-runs/{id}/...
    conn.execute(
        sa.text("""
            UPDATE files
            SET source_type = 'plot_archive',
                source_pipeline_run_id = (
                    SELECT pr.id FROM pipeline_runs pr
                    WHERE pr.id = (
                        regexp_match(files.gcs_uri, 'pipeline-runs/(\d+)/')
                    )[1]::int
                    LIMIT 1
                )
            WHERE files.source_type = 'upload'
              AND files.uploader_user_id IS NULL
              AND files.file_type IN ('png', 'svg', 'pdf')
              AND files.gcs_uri LIKE '%/pipeline-runs/%'
        """)
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            UPDATE files
            SET source_type = 'upload',
                source_pipeline_run_id = NULL
            WHERE source_type IN ('qc_dashboard', 'plot_archive')
        """)
    )
