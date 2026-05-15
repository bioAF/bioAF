"""LLM integration v1: provider config, agent reviews, jobs, RBAC seed.

Revision ID: 081
Revises: 080
Create Date: 2026-05-15

Additive only (ADR-052 through ADR-055):
- llm_provider_config (api_key encrypted via EncryptedString)
- agent_review_jobs (operational record, partial unique index for debounce)
- agent_reviews (user-facing record, one-to-one with agent_review_jobs)
- Backfill role_permissions: admin gets llm_integration:{configure,use};
  comp_bio gets llm_integration:use, in every existing organization's roles.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "081"
down_revision = "080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # llm_provider_config
    # ------------------------------------------------------------------
    op.create_table(
        "llm_provider_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        # EncryptedString stores Fernet ciphertext as TEXT.
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("api_key_prefix_last5", sa.String(5), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_provider_config_organization_id",
        "llm_provider_config",
        ["organization_id"],
    )
    op.create_index(
        "uq_llm_provider_config_org_provider",
        "llm_provider_config",
        ["organization_id", "provider"],
        unique=True,
    )
    # Partial unique index: at most one is_active=true row per org.
    op.execute(
        "CREATE UNIQUE INDEX uq_llm_provider_config_one_active_per_org "
        "ON llm_provider_config (organization_id) WHERE is_active = true"
    )

    # ------------------------------------------------------------------
    # agent_review_jobs
    # ------------------------------------------------------------------
    op.create_table(
        "agent_review_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("triggered_by_user_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("review_type", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("prompt_template_version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("included_run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "include_html_report_run_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "artifact_gcs_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("agent_review_id", sa.BigInteger(), nullable=True),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_review_jobs_organization_id",
        "agent_review_jobs",
        ["organization_id"],
    )
    op.create_index(
        "ix_agent_review_jobs_org_status",
        "agent_review_jobs",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_agent_review_jobs_entity",
        "agent_review_jobs",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "ix_agent_review_jobs_agent_review_id",
        "agent_review_jobs",
        ["agent_review_id"],
    )
    # Partial unique index for debounce: at most one in-flight per
    # (entity_type, entity_id, review_type).
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_review_jobs_inflight_debounce "
        "ON agent_review_jobs (entity_type, entity_id, review_type) "
        "WHERE status IN ('pending', 'building_artifacts', 'submitted')"
    )

    # ------------------------------------------------------------------
    # agent_reviews
    # ------------------------------------------------------------------
    op.create_table(
        "agent_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("triggered_by_user_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("included_run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("review_type", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("prompt_template_version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "artifact_gcs_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("agent_review_job_id", sa.BigInteger(), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["dismissed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["agent_review_job_id"], ["agent_review_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_review_job_id", name="uq_agent_reviews_job_id"),
    )
    op.create_index(
        "ix_agent_reviews_organization_id",
        "agent_reviews",
        ["organization_id"],
    )
    op.create_index(
        "ix_agent_reviews_org_entity_created",
        "agent_reviews",
        ["organization_id", "entity_type", "entity_id", "created_at"],
    )
    op.create_index(
        "ix_agent_reviews_org_status",
        "agent_reviews",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_agent_reviews_org_dismissed",
        "agent_reviews",
        ["organization_id", "dismissed_at"],
    )

    # ------------------------------------------------------------------
    # Backfill RBAC permissions for all existing organizations.
    # ------------------------------------------------------------------
    # admin role: llm_integration:configure and llm_integration:use
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, 'llm_integration', 'configure'
        FROM roles r
        WHERE r.name = 'admin' AND r.is_system = true
        AND NOT EXISTS (
            SELECT 1 FROM role_permissions rp
            WHERE rp.role_id = r.id
              AND rp.resource = 'llm_integration'
              AND rp.action = 'configure'
        )
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, 'llm_integration', 'use'
        FROM roles r
        WHERE r.name = 'admin' AND r.is_system = true
        AND NOT EXISTS (
            SELECT 1 FROM role_permissions rp
            WHERE rp.role_id = r.id
              AND rp.resource = 'llm_integration'
              AND rp.action = 'use'
        )
        """
    )
    # comp_bio role: llm_integration:use
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, 'llm_integration', 'use'
        FROM roles r
        WHERE r.name = 'comp_bio' AND r.is_system = true
        AND NOT EXISTS (
            SELECT 1 FROM role_permissions rp
            WHERE rp.role_id = r.id
              AND rp.resource = 'llm_integration'
              AND rp.action = 'use'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE resource = 'llm_integration' AND action IN ('configure', 'use')")
    op.drop_index("ix_agent_reviews_org_dismissed", table_name="agent_reviews")
    op.drop_index("ix_agent_reviews_org_status", table_name="agent_reviews")
    op.drop_index("ix_agent_reviews_org_entity_created", table_name="agent_reviews")
    op.drop_index("ix_agent_reviews_organization_id", table_name="agent_reviews")
    op.drop_table("agent_reviews")

    op.execute("DROP INDEX IF EXISTS uq_agent_review_jobs_inflight_debounce")
    op.drop_index("ix_agent_review_jobs_agent_review_id", table_name="agent_review_jobs")
    op.drop_index("ix_agent_review_jobs_entity", table_name="agent_review_jobs")
    op.drop_index("ix_agent_review_jobs_org_status", table_name="agent_review_jobs")
    op.drop_index("ix_agent_review_jobs_organization_id", table_name="agent_review_jobs")
    op.drop_table("agent_review_jobs")

    op.execute("DROP INDEX IF EXISTS uq_llm_provider_config_one_active_per_org")
    op.drop_index("uq_llm_provider_config_org_provider", table_name="llm_provider_config")
    op.drop_index("ix_llm_provider_config_organization_id", table_name="llm_provider_config")
    op.drop_table("llm_provider_config")
