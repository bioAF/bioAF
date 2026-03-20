"""Create orphaned_resources table for tracking partially-created GCP resources.

Revision ID: 032
Revises: 031
Create Date: 2026-03-20

Tracks GKE clusters and GCS buckets left behind by failed Terraform
deployments so admins can clean them up from the UI.
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orphaned_resources",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_name", sa.String(255), nullable=False),
        sa.Column("gcp_project_id", sa.String(255), nullable=False),
        sa.Column("gcp_zone", sa.String(100), nullable=True),
        sa.Column("stack_uid", sa.String(20), nullable=False),
        sa.Column(
            "terraform_run_id",
            sa.Integer,
            sa.ForeignKey("terraform_runs.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="detected"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_orphaned_resources_status", "orphaned_resources", ["status"])
    op.create_index("ix_orphaned_resources_stack_uid", "orphaned_resources", ["stack_uid"])


def downgrade() -> None:
    op.drop_index("ix_orphaned_resources_stack_uid", table_name="orphaned_resources")
    op.drop_index("ix_orphaned_resources_status", table_name="orphaned_resources")
    op.drop_table("orphaned_resources")
