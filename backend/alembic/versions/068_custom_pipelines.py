"""Add custom pipelines tables.

Revision ID: 068
Revises: 067

Creates custom_pipelines, custom_pipeline_versions, and custom_pipeline_variables
tables. Adds custom_pipeline_id FK to pipeline_catalog and
custom_pipeline_version_id FK to pipeline_runs.
"""

import sqlalchemy as sa
from alembic import op

revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_pipelines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("pipeline_key", sa.String(100), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "pipeline_key", name="uq_custom_pipeline_org_key"),
    )

    op.create_table(
        "custom_pipeline_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("custom_pipeline_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("code_source_type", sa.String(20), nullable=False),
        sa.Column("github_repo_id", sa.Integer(), nullable=True),
        sa.Column("code_content", sa.Text(), nullable=True),
        sa.Column("entrypoint_command", sa.Text(), nullable=False),
        sa.Column("environment_version_id", sa.Integer(), nullable=False),
        sa.Column("cpu_request", sa.String(20), nullable=False, server_default="2"),
        sa.Column("memory_request", sa.String(20), nullable=False, server_default="8Gi"),
        sa.Column("log_file_path", sa.String(500), nullable=True),
        sa.Column("version_trigger", sa.String(20), nullable=False, server_default="user"),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["custom_pipeline_id"], ["custom_pipelines.id"]),
        sa.ForeignKeyConstraint(["github_repo_id"], ["github_repos.id"]),
        sa.ForeignKeyConstraint(["environment_version_id"], ["environment_versions.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("custom_pipeline_id", "version_number", name="uq_custom_pipeline_version_number"),
    )

    op.create_table(
        "custom_pipeline_variables",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("custom_pipeline_version_id", sa.Integer(), nullable=False),
        sa.Column("variable_name", sa.String(255), nullable=False),
        sa.Column("default_value", sa.Text(), nullable=True),
        sa.Column("variable_type", sa.String(50), nullable=False, server_default="string"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["custom_pipeline_version_id"], ["custom_pipeline_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "pipeline_catalog",
        sa.Column("custom_pipeline_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pipeline_catalog_custom_pipeline_id",
        "pipeline_catalog",
        "custom_pipelines",
        ["custom_pipeline_id"],
        ["id"],
    )

    op.add_column(
        "pipeline_runs",
        sa.Column("custom_pipeline_version_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pipeline_runs_custom_pipeline_version_id",
        "pipeline_runs",
        "custom_pipeline_versions",
        ["custom_pipeline_version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_pipeline_runs_custom_pipeline_version_id", "pipeline_runs", type_="foreignkey")
    op.drop_column("pipeline_runs", "custom_pipeline_version_id")
    op.drop_constraint("fk_pipeline_catalog_custom_pipeline_id", "pipeline_catalog", type_="foreignkey")
    op.drop_column("pipeline_catalog", "custom_pipeline_id")
    op.drop_table("custom_pipeline_variables")
    op.drop_table("custom_pipeline_versions")
    op.drop_table("custom_pipelines")
