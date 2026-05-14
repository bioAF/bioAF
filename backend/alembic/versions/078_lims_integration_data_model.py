"""LIMS integration data-model additions.

Revision ID: 078
Revises: 077
Create Date: 2026-05-13

Additive only (ADR-050):
- projects.external_id with partial unique index (org_id, external_id)
- experiments.external_id with partial unique index (org_id, external_id)
- project_custom_fields table (mirror of sample_custom_fields)
- idempotency_keys table for Idempotency-Key replay (24h TTL)
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "078"
down_revision = "077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("external_id", sa.String(255), nullable=True))
    op.create_index(
        "uq_projects_org_external_id",
        "projects",
        ["organization_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.add_column("experiments", sa.Column("external_id", sa.String(255), nullable=True))
    op.create_index(
        "uq_experiments_org_external_id",
        "experiments",
        ["organization_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.create_table(
        "project_custom_fields",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("field_value", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_custom_fields_project_id", "project_custom_fields", ["project_id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("api_key_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_id", "key", name="uq_idempotency_api_key"),
    )
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_project_custom_fields_project_id", table_name="project_custom_fields")
    op.drop_table("project_custom_fields")
    op.drop_index("uq_experiments_org_external_id", table_name="experiments")
    op.drop_column("experiments", "external_id")
    op.drop_index("uq_projects_org_external_id", table_name="projects")
    op.drop_column("projects", "external_id")
