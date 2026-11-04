"""Custom runs store their pipeline's key; custom versions default to a schedulable request.

Revision ID: 131
Revises: 130
Create Date: 2026-11-04

A custom run's pipeline is known exactly, through its version, so its key is backfilled (unlike a
catalog run, whose only record is a display name; those stay null). Custom versions now default to
1 CPU / 4Gi: the old 2 CPU / 8Gi never fit a pipeline-head node, so a version left at the default
stayed Pending forever.
"""

from alembic import op

revision = "131"
down_revision = "130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE pipeline_runs AS r
        SET pipeline_key = p.pipeline_key
        FROM custom_pipeline_versions AS v
        JOIN custom_pipelines AS p ON p.id = v.custom_pipeline_id
        WHERE r.custom_pipeline_version_id = v.id
          AND r.pipeline_key IS NULL
        """
    )
    op.alter_column("custom_pipeline_versions", "cpu_request", server_default="1")
    op.alter_column("custom_pipeline_versions", "memory_request", server_default="4Gi")


def downgrade() -> None:
    op.alter_column("custom_pipeline_versions", "memory_request", server_default="8Gi")
    op.alter_column("custom_pipeline_versions", "cpu_request", server_default="2")
    op.execute("UPDATE pipeline_runs SET pipeline_key = NULL WHERE custom_pipeline_version_id IS NOT NULL")
