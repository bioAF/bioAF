"""Phase 6: GitOps + Package Management tables.

Revision ID: 003
Revises: 002
Create Date: 2026-03-06

New tables: gitops_repos, environments, environment_packages,
            environment_changes, template_notebooks
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # gitops_repos table
    # ---------------------------------------------------------------
    op.create_table(
        "gitops_repos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("github_repo_url", sa.String(length=500), nullable=False),
        sa.Column("github_repo_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )

    # ---------------------------------------------------------------
    # environments table
    # ---------------------------------------------------------------
    op.create_table(
        "environments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("env_type", sa.String(length=50), nullable=False),
        sa.Column("yaml_path", sa.String(length=500), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("jupyter_kernel_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_environments_org_name"),
    )
    op.create_index("idx_environments_org", "environments", ["organization_id"])
    op.create_index("idx_environments_type", "environments", ["env_type"])

    # ---------------------------------------------------------------
    # environment_packages table
    # ---------------------------------------------------------------
    op.create_table(
        "environment_packages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("package_name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=True),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("environment_id", "package_name", "source", name="uq_envpkg_env_name_source"),
    )
    op.create_index("idx_envpkg_environment", "environment_packages", ["environment_id"])
    op.create_index("idx_envpkg_name", "environment_packages", ["package_name"])

    # ---------------------------------------------------------------
    # environment_changes table
    # ---------------------------------------------------------------
    op.create_table(
        "environment_changes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("change_type", sa.String(length=50), nullable=False),
        sa.Column("package_name", sa.String(length=255), nullable=True),
        sa.Column("old_version", sa.String(length=100), nullable=True),
        sa.Column("new_version", sa.String(length=100), nullable=True),
        sa.Column("git_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("commit_message", sa.Text(), nullable=True),
        sa.Column("reconciled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_envchanges_org", "environment_changes", ["organization_id"])
    op.create_index("idx_envchanges_env", "environment_changes", ["environment_id"])
    op.create_index("idx_envchanges_created", "environment_changes", ["created_at"])

    # ---------------------------------------------------------------
    # template_notebooks table
    # ---------------------------------------------------------------
    op.create_table(
        "template_notebooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("notebook_path", sa.String(length=500), nullable=False),
        sa.Column("parameters_json", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("compatible_with", sa.String(length=255), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_template_notebooks_org", "template_notebooks", ["organization_id"])

    # Grant permissions (conditionally: bioaf_app role may not exist in dev/POC)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bioaf_app') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON gitops_repos, environments, environment_packages, environment_changes, template_notebooks TO bioaf_app';
                EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bioaf_app';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.drop_index("idx_template_notebooks_org")
    op.drop_table("template_notebooks")

    op.drop_index("idx_envchanges_created")
    op.drop_index("idx_envchanges_env")
    op.drop_index("idx_envchanges_org")
    op.drop_table("environment_changes")

    op.drop_index("idx_envpkg_name")
    op.drop_index("idx_envpkg_environment")
    op.drop_table("environment_packages")

    op.drop_index("idx_environments_type")
    op.drop_index("idx_environments_org")
    op.drop_table("environments")

    op.drop_table("gitops_repos")
