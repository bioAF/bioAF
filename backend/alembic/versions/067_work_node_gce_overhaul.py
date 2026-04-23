"""Work node GCE overhaul (ADR-043).

Revision ID: 067
Revises: 066
Create Date: 2026-04-19

Adds environment_type to environments, GCE columns to compute_sessions,
and creates the github_repos table.
"""

import sqlalchemy as sa
from alembic import op

revision = "067"
down_revision = "066"


def upgrade() -> None:
    # 1. Add environment_type to environments
    op.add_column(
        "environments",
        sa.Column("environment_type", sa.String(50), nullable=False, server_default="notebook"),
    )

    # 2. Add GCE columns to compute_sessions
    op.add_column("compute_sessions", sa.Column("gce_instance_name", sa.String(255), nullable=True))
    op.add_column("compute_sessions", sa.Column("gce_zone", sa.String(100), nullable=True))
    op.add_column("compute_sessions", sa.Column("gce_project_id", sa.String(255), nullable=True))
    op.add_column("compute_sessions", sa.Column("github_repo_ids", sa.JSON(), nullable=True))

    # 3. Create github_repos table
    op.create_table(
        "github_repos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("git_ssh_url", sa.String(500), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "git_ssh_url", name="uq_github_repo_user_url"),
    )
    op.create_index("ix_github_repos_user_id", "github_repos", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_github_repos_user_id", table_name="github_repos")
    op.drop_table("github_repos")
    op.drop_column("compute_sessions", "github_repo_ids")
    op.drop_column("compute_sessions", "gce_project_id")
    op.drop_column("compute_sessions", "gce_zone")
    op.drop_column("compute_sessions", "gce_instance_name")
    op.drop_column("environments", "environment_type")
