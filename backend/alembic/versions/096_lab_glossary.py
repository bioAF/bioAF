"""Lab Knowledge Phase B: Lab Glossary (ADR-062).

Revision ID: 096
Revises: 095
Create Date: 2026-06-05

Creates the glossary tables: terms, term history, rejected proposals, scan jobs,
and scan proposals. RBAC for lab_glossary (view for all system roles, manage and
delete for admin) was already registered in migration 095, so no role backfill
happens here.
"""

import sqlalchemy as sa
from alembic import op

revision = "096"
down_revision = "095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lab_glossary_terms",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("term", sa.String(length=500), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("aliases", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('manual', 'import', 'llm_scan')", name="ck_lab_glossary_terms_source"),
    )
    op.create_index("idx_lab_glossary_terms_org", "lab_glossary_terms", ["organization_id"])
    # Case-insensitive uniqueness so "Passage" and "passage" cannot both exist;
    # the duplicate check in the service is case-insensitive to match.
    op.execute(
        "CREATE UNIQUE INDEX uq_lab_glossary_terms_org_lower_term ON lab_glossary_terms (organization_id, lower(term))"
    )

    op.create_table(
        "lab_glossary_term_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("lab_glossary_terms.id"), nullable=False),
        sa.Column("previous_definition", sa.Text(), nullable=False),
        sa.Column("previous_aliases", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("previous_category", sa.String(length=200), nullable=True),
        sa.Column("previous_context", sa.Text(), nullable=True),
        sa.Column("changed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_lab_glossary_term_history_term", "lab_glossary_term_history", ["term_id"])

    op.create_table(
        "lab_glossary_rejected_proposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("term", sa.String(length=500), nullable=False),
        sa.Column("proposed_definition", sa.Text(), nullable=False),
        sa.Column("proposed_source", sa.String(length=50), nullable=False),
        sa.Column("rejected_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rejected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_lab_glossary_rejected_org_term", "lab_glossary_rejected_proposals", ["organization_id", "term"]
    )

    op.create_table(
        "lab_glossary_scan_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("scan_type", sa.String(length=20), nullable=False),
        sa.Column("scan_input", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("proposed_new_count", sa.Integer(), nullable=True),
        sa.Column("proposed_changed_count", sa.Integer(), nullable=True),
        sa.Column("accepted_count", sa.Integer(), nullable=True),
        sa.Column("rejected_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("initiated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scan_type IN ('document', 'topic', 'platform_wide', 'import')",
            name="ck_lab_glossary_scan_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'complete', 'failed')",
            name="ck_lab_glossary_scan_jobs_status",
        ),
    )
    op.create_index("idx_lab_glossary_scan_jobs_org", "lab_glossary_scan_jobs", ["organization_id"])

    op.create_table(
        "lab_glossary_scan_proposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scan_job_id", sa.Integer(), sa.ForeignKey("lab_glossary_scan_jobs.id"), nullable=False),
        sa.Column("term", sa.String(length=500), nullable=False),
        sa.Column("proposed_definition", sa.Text(), nullable=False),
        sa.Column("proposed_aliases", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("proposed_category", sa.String(length=200), nullable=True),
        sa.Column("proposed_context", sa.Text(), nullable=True),
        sa.Column("proposal_type", sa.String(length=10), nullable=False),
        sa.Column("existing_term_id", sa.Integer(), sa.ForeignKey("lab_glossary_terms.id"), nullable=True),
        sa.Column("source_description", sa.Text(), nullable=True),
        sa.Column("previously_rejected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("review_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("proposal_type IN ('new', 'changed')", name="ck_lab_glossary_proposals_type"),
        sa.CheckConstraint(
            "review_status IN ('pending', 'accepted', 'kept_existing', 'rejected')",
            name="ck_lab_glossary_proposals_review_status",
        ),
    )
    op.create_index("idx_lab_glossary_proposals_job", "lab_glossary_scan_proposals", ["scan_job_id"])


def downgrade() -> None:
    op.drop_index("idx_lab_glossary_proposals_job", table_name="lab_glossary_scan_proposals")
    op.drop_table("lab_glossary_scan_proposals")
    op.drop_index("idx_lab_glossary_scan_jobs_org", table_name="lab_glossary_scan_jobs")
    op.drop_table("lab_glossary_scan_jobs")
    op.drop_index("idx_lab_glossary_rejected_org_term", table_name="lab_glossary_rejected_proposals")
    op.drop_table("lab_glossary_rejected_proposals")
    op.drop_index("idx_lab_glossary_term_history_term", table_name="lab_glossary_term_history")
    op.drop_table("lab_glossary_term_history")
    op.execute("DROP INDEX IF EXISTS uq_lab_glossary_terms_org_lower_term")
    op.drop_index("idx_lab_glossary_terms_org", table_name="lab_glossary_terms")
    op.drop_table("lab_glossary_terms")
