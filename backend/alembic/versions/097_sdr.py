"""Lab Knowledge Phase C: Scientific Decision Records (ADR-059, ADR-063, ADR-064).

Revision ID: 097
Revises: 096
Create Date: 2026-06-05

Creates the SDR tables (sdr_categories, scientific_decision_records,
sdr_status_transitions) and seeds the default SDR category vocabulary for every
existing organization. RBAC for the ``sdr`` resource (view for all system roles,
author for admin + comp_bio, manage for admin) was already registered in
migration 095, so no role backfill happens here. New orgs get the default
categories from the bootstrap path (seed_default_sdr_categories).
"""

import sqlalchemy as sa
from alembic import op

revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None

DEFAULT_CATEGORIES = ["Protocol/Methods", "Analysis", "QC Thresholds", "Vendor/Reagent", "Operational"]


def upgrade() -> None:
    op.create_table(
        "sdr_categories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_sdr_categories_org_name"),
    )
    op.create_index("idx_sdr_categories_org", "sdr_categories", ["organization_id"])

    op.create_table(
        "scientific_decision_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("sdr_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("sdr_categories.id"), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("trigger_date", sa.Date(), nullable=True),
        sa.Column("trigger_warning_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "superseded_by_sdr_id",
            sa.Integer(),
            sa.ForeignKey("scientific_decision_records.id"),
            nullable=True,
        ),
        sa.Column(
            "supersedes_sdr_id",
            sa.Integer(),
            sa.ForeignKey("scientific_decision_records.id"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "sdr_number", name="uq_sdr_org_number"),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'flagged_for_review', 'superseded', 'repealed')",
            name="ck_sdr_status",
        ),
    )
    op.create_index("idx_sdr_org", "scientific_decision_records", ["organization_id"])
    op.create_index("idx_sdr_trigger", "scientific_decision_records", ["status", "trigger_date"])

    op.create_table(
        "sdr_status_transitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sdr_id", sa.Integer(), sa.ForeignKey("scientific_decision_records.id"), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("transitioned_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_sdr_transitions_sdr", "sdr_status_transitions", ["sdr_id"])

    # Seed default categories for every existing org. created_by_user_id is set to
    # the org's lowest user id, matching the tag seed in migration 095.
    for name in DEFAULT_CATEGORIES:
        op.execute(
            sa.text(
                """
                INSERT INTO sdr_categories (organization_id, name, created_by_user_id)
                SELECT o.id, :name, (
                    SELECT u.id FROM users u
                    WHERE u.organization_id = o.id
                    ORDER BY u.id ASC LIMIT 1
                )
                FROM organizations o
                WHERE EXISTS (SELECT 1 FROM users u2 WHERE u2.organization_id = o.id)
                  AND NOT EXISTS (
                    SELECT 1 FROM sdr_categories c
                    WHERE c.organization_id = o.id AND c.name = :name
                  )
                """
            ).bindparams(name=name)
        )


def downgrade() -> None:
    op.drop_index("idx_sdr_transitions_sdr", table_name="sdr_status_transitions")
    op.drop_table("sdr_status_transitions")
    op.drop_index("idx_sdr_trigger", table_name="scientific_decision_records")
    op.drop_index("idx_sdr_org", table_name="scientific_decision_records")
    op.drop_table("scientific_decision_records")
    op.drop_index("idx_sdr_categories_org", table_name="sdr_categories")
    op.drop_table("sdr_categories")
