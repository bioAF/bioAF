"""Rename notebook_sessions to compute_sessions and add work node columns (ADR-034).

Renames the table, adds environment_version_id FK, machine_type, data_mount_paths,
heartbeat_at, and heartbeat_token columns for SSH work node support.

Revision ID: 038
Revises: 037
Create Date: 2026-03-23
"""

import sqlalchemy as sa
from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Rename table
    op.rename_table("notebook_sessions", "compute_sessions")

    # 2. Add new columns for work node support
    op.add_column(
        "compute_sessions",
        sa.Column("environment_version_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "compute_sessions",
        sa.Column("machine_type", sa.String(100), nullable=True),
    )
    op.add_column(
        "compute_sessions",
        sa.Column("data_mount_paths", sa.JSON(), nullable=True),
    )
    op.add_column(
        "compute_sessions",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "compute_sessions",
        sa.Column("heartbeat_token", sa.String(255), nullable=True),
    )

    # 3. Add FK constraint for environment_version_id
    op.create_foreign_key(
        "fk_compute_sessions_env_version",
        "compute_sessions",
        "environment_versions",
        ["environment_version_id"],
        ["id"],
    )

    # 4. Add indexes
    op.create_index(
        "ix_compute_sessions_env_version_id",
        "compute_sessions",
        ["environment_version_id"],
    )
    op.create_index(
        "ix_compute_sessions_session_type",
        "compute_sessions",
        ["session_type"],
    )
    op.create_index(
        "ix_compute_sessions_heartbeat_at",
        "compute_sessions",
        ["heartbeat_at"],
    )

    # 5. Update FK references in analysis_snapshots if it references notebook_sessions
    # The FK constraint names from migration 018 need updating
    conn = op.get_bind()
    # Check if analysis_snapshots has a FK to the old table name
    result = conn.execute(
        sa.text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name = 'analysis_snapshots' AND constraint_type = 'FOREIGN KEY' "
            "AND constraint_name LIKE '%notebook_session%'"
        )
    )
    for row in result.fetchall():
        op.drop_constraint(row[0], "analysis_snapshots", type_="foreignkey")
        op.create_foreign_key(
            "fk_analysis_snapshots_compute_session",
            "analysis_snapshots",
            "compute_sessions",
            ["notebook_session_id"],
            ["id"],
        )


def downgrade() -> None:
    # Drop new indexes
    op.drop_index("ix_compute_sessions_heartbeat_at", table_name="compute_sessions")
    op.drop_index("ix_compute_sessions_session_type", table_name="compute_sessions")
    op.drop_index("ix_compute_sessions_env_version_id", table_name="compute_sessions")

    # Drop FK
    op.drop_constraint("fk_compute_sessions_env_version", "compute_sessions", type_="foreignkey")

    # Drop new columns
    op.drop_column("compute_sessions", "heartbeat_token")
    op.drop_column("compute_sessions", "heartbeat_at")
    op.drop_column("compute_sessions", "data_mount_paths")
    op.drop_column("compute_sessions", "machine_type")
    op.drop_column("compute_sessions", "environment_version_id")

    # Rename table back
    op.rename_table("compute_sessions", "notebook_sessions")
