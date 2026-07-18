"""ValidationStudy aggregate table (lit_validation A1, spec-02).

Revision ID: 108
Revises: 107
Create Date: 2026-07-01

Additive only. Creates validation_studies, the aggregate root for one paper-validation attempt.
Mirrors app/models/validation_study.py (the source of truth the test suite builds via create_all).
The reproduction_plan_id column is intentionally a plain nullable Integer (no FK) until the
reproduction_plans table lands in a later increment.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "108"
down_revision = "107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_studies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("paper_id", sa.BigInteger(), nullable=True),
        sa.Column("source_doi", sa.String(512), nullable=True),
        sa.Column("source_accession", sa.String(255), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("state", sa.String(50), nullable=False, server_default="requested"),
        sa.Column("classification", sa.String(50), nullable=True),
        sa.Column("experiment_id", sa.Integer(), nullable=True),
        sa.Column("reproduction_plan_id", sa.Integer(), nullable=True),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["paper_id"], ["literature_papers.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid", name="uq_validation_studies_uuid"),
    )
    op.create_index(
        "ix_validation_studies_organization_id",
        "validation_studies",
        ["organization_id"],
    )
    op.create_index(
        "ix_validation_studies_paper_id",
        "validation_studies",
        ["paper_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_validation_studies_paper_id", table_name="validation_studies")
    op.drop_index("ix_validation_studies_organization_id", table_name="validation_studies")
    op.drop_table("validation_studies")
