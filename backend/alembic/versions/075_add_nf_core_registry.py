"""Add nf_core_registry_pipeline + nf_core_registry_refresh tables.

Revision ID: 075
Revises: 074
Create Date: 2026-05-12

Caches the nf-co.re/pipelines.json registry locally so the catalog UI can
browse and install nf-core pipelines without a round trip to nf-co.re on
every page load.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nf_core_registry_pipeline",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("topics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("stars", sa.Integer(), nullable=True),
        sa.Column("default_branch", sa.String(length=100), nullable=True),
        sa.Column("releases_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("latest_release", sa.String(length=50), nullable=True),
        sa.Column(
            "archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_nf_core_registry_pipeline_name"),
    )
    op.create_index(
        "ix_nf_core_registry_pipeline_archived",
        "nf_core_registry_pipeline",
        ["archived"],
    )
    op.create_index(
        "ix_nf_core_registry_pipeline_topics",
        "nf_core_registry_pipeline",
        ["topics"],
        postgresql_using="gin",
    )

    op.create_table(
        "nf_core_registry_refresh",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("nf_core_registry_refresh")
    op.drop_index(
        "ix_nf_core_registry_pipeline_topics",
        table_name="nf_core_registry_pipeline",
    )
    op.drop_index(
        "ix_nf_core_registry_pipeline_archived",
        table_name="nf_core_registry_pipeline",
    )
    op.drop_table("nf_core_registry_pipeline")
