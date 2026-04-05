"""Notebook file lifecycle (ADR-040, ADR-041).

Revision ID: 057
Revises: 056

Adds build_number to environment_versions for rebuild versioning (v1 -> v1.1).
Adds gcs_output_prefix to compute_sessions for output persistence tracking.
Adds unique constraint on (environment_id, version_number, build_number).
"""

from alembic import op
import sqlalchemy as sa

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADR-041: build_number for environment rebuild versioning
    op.add_column(
        "environment_versions",
        sa.Column("build_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint(
        "uq_env_version_build",
        "environment_versions",
        ["environment_id", "version_number", "build_number"],
    )

    # ADR-040: track where session outputs were persisted
    op.add_column(
        "compute_sessions",
        sa.Column("gcs_output_prefix", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("compute_sessions", "gcs_output_prefix")
    op.drop_constraint("uq_env_version_build", "environment_versions", type_="unique")
    op.drop_column("environment_versions", "build_number")
