"""Add github_repo_name to experiments/projects and git columns to compute_sessions.

Revision ID: 048
Revises: 047
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("github_repo_name", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("github_repo_name", sa.String(200), nullable=True))
    op.add_column("compute_sessions", sa.Column("git_branch_name", sa.String(200), nullable=True))
    op.add_column("compute_sessions", sa.Column("git_commit_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("compute_sessions", "git_commit_hash")
    op.drop_column("compute_sessions", "git_branch_name")
    op.drop_column("projects", "github_repo_name")
    op.drop_column("experiments", "github_repo_name")
