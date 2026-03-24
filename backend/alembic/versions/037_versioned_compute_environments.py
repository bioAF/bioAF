"""Replace environment tracking tables with versioned compute environments (ADR-033).

Drops old environments, environment_packages, environment_changes tables.
Creates new environments and environment_versions tables.
Seeds a "Default scRNA-seq" environment with the embedded Dockerfile as version 1.

Revision ID: 037
Revises: 036
Create Date: 2026-03-23
"""

import sqlalchemy as sa
from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None

# The current embedded Dockerfile from NotebookImageService, seeded as version 1
DEFAULT_DOCKERFILE = """\
FROM jupyter/scipy-notebook:latest

USER root

# System dependencies for R, HDF5, and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \\
    libhdf5-dev libcurl4-openssl-dev libssl-dev libxml2-dev \\
    cmake r-base r-base-dev \\
    && rm -rf /var/lib/apt/lists/*

# Python scRNA-seq packages
RUN pip install --no-cache-dir \\
    scanpy anndata scvi-tools leidenalg \\
    pandas numpy matplotlib seaborn plotly \\
    umap-learn bbknn scrublet \\
    google-cloud-storage

# R packages (core set for Seurat and Bioconductor)
RUN R -e "install.packages(c('Seurat', 'ggplot2', 'tidyverse', 'pheatmap', 'devtools'), repos='https://cloud.r-project.org')"
RUN R -e "if (!requireNamespace('BiocManager', quietly=TRUE)) install.packages('BiocManager', repos='https://cloud.r-project.org'); BiocManager::install(c('SingleCellExperiment', 'scater', 'scran'))"

# RStudio Server
RUN apt-get update && apt-get install -y --no-install-recommends gdebi-core wget \\
    && wget -q https://download2.rstudio.org/server/jammy/amd64/rstudio-server-2024.04.2-764-amd64.deb \\
    && gdebi -n rstudio-server-2024.04.2-764-amd64.deb \\
    && rm rstudio-server-*.deb \\
    && rm -rf /var/lib/apt/lists/*

# gsutil for GCS home directory sync
RUN pip install --no-cache-dir gsutil

USER ${NB_UID}

WORKDIR /home/jovyan
"""


def upgrade() -> None:
    # 1. Drop old tables (order matters for FK constraints)
    op.drop_table("environment_changes")
    op.drop_table("environment_packages")
    op.drop_table("environments")

    # 2. Create new environments table
    op.create_table(
        "environments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(50), nullable=False, server_default="team"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
    )
    op.create_index("ix_environments_org_id", "environments", ["organization_id"])

    # 3. Create environment_versions table
    op.create_table(
        "environment_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("definition_format", sa.String(50), nullable=False),
        sa.Column("definition_content", sa.Text(), nullable=False),
        sa.Column("build_id", sa.String(255), nullable=True),
        sa.Column("image_uri", sa.String(500), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
    )
    op.create_index("ix_env_versions_env_id", "environment_versions", ["environment_id"])
    op.create_index("ix_env_versions_status", "environment_versions", ["status"])

    # 4. Seed default environment per organization
    conn = op.get_bind()
    orgs = conn.execute(sa.text("SELECT id FROM organizations")).fetchall()

    for (org_id,) in orgs:
        # Find the first admin user in this org to use as created_by
        admin_row = conn.execute(
            sa.text(
                "SELECT u.id FROM users u "
                "JOIN roles r ON r.id = u.role_id "
                "WHERE u.organization_id = :org_id AND r.name = 'admin' "
                "LIMIT 1"
            ),
            {"org_id": org_id},
        ).fetchone()
        if not admin_row:
            continue
        admin_user_id = admin_row[0]

        # Read existing image URI from platform_config (if a build already succeeded)
        image_row = conn.execute(
            sa.text("SELECT value FROM platform_config WHERE key = 'bioaf_scrna_image'")
        ).fetchone()
        image_uri = image_row[0] if image_row and image_row[0] != "null" else None

        # Create the default environment
        result = conn.execute(
            sa.text(
                "INSERT INTO environments (name, description, organization_id, created_by_user_id, visibility) "
                "VALUES (:name, :desc, :org_id, :user_id, 'organization') RETURNING id"
            ),
            {
                "name": "Default scRNA-seq",
                "desc": "Default single-cell RNA-seq analysis environment with scanpy, Seurat, and RStudio",
                "org_id": org_id,
                "user_id": admin_user_id,
            },
        )
        row = result.fetchone()
        assert row is not None
        env_id = row[0]

        # Seed version 1 with the embedded Dockerfile
        version_status = "ready" if image_uri else "draft"
        conn.execute(
            sa.text(
                "INSERT INTO environment_versions "
                "(environment_id, version_number, status, definition_format, definition_content, "
                "image_uri, created_by_user_id) "
                "VALUES (:env_id, 1, :status, 'dockerfile', :content, :image_uri, :user_id)"
            ),
            {
                "env_id": env_id,
                "status": version_status,
                "content": DEFAULT_DOCKERFILE,
                "image_uri": image_uri,
                "user_id": admin_user_id,
            },
        )


def downgrade() -> None:
    # Drop new tables
    op.drop_index("ix_env_versions_status", table_name="environment_versions")
    op.drop_index("ix_env_versions_env_id", table_name="environment_versions")
    op.drop_table("environment_versions")
    op.drop_index("ix_environments_org_id", table_name="environments")
    op.drop_table("environments")

    # Recreate old tables
    op.create_table(
        "environments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("env_type", sa.String(50), nullable=False),
        sa.Column("yaml_path", sa.String(500), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("jupyter_kernel_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
    )

    op.create_table(
        "environment_packages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("package_name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(100), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"]),
    )

    op.create_table(
        "environment_changes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("change_type", sa.String(50), nullable=False),
        sa.Column("package_name", sa.String(255), nullable=True),
        sa.Column("old_version", sa.String(100), nullable=True),
        sa.Column("new_version", sa.String(100), nullable=True),
        sa.Column("git_commit_sha", sa.String(64), nullable=True),
        sa.Column("commit_message", sa.Text(), nullable=True),
        sa.Column("reconciled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
