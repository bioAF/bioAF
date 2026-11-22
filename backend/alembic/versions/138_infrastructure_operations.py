"""Durable infrastructure operations.

Revision ID: 138
Revises: 137
Create Date: 2026-11-21

Adds ``infrastructure_operations``, one row per accepted infrastructure action, whose unique
``exclusive_slot`` admits one operation at a time, and links each Terraform run to its operation.
Existing Terraform runs keep a NULL operation. No operation is created or inferred here.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "138"
down_revision = "137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "infrastructure_operations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("exclusive_slot", sa.SmallInteger, nullable=True),
        sa.Column("started_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "started_by_api_key_id", sa.BigInteger, sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("principal", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=True),
        sa.Column("inputs", postgresql.JSONB, nullable=True),
        sa.Column("deployment_generation", postgresql.JSONB, nullable=True),
        sa.Column("runner", postgresql.JSONB, nullable=True),
        sa.Column("child_run_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("external_builds", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("steps", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("phase", sa.String(120), nullable=True),
        sa.Column("error_category", sa.String(48), nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("partial_effects", postgresql.JSONB, nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_requested_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("cancel_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recovery_reason", sa.Text, nullable=True),
        sa.Column("outcome_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("exclusive_slot", name="uq_infrastructure_operations_exclusive_slot"),
        sa.CheckConstraint("exclusive_slot IS NULL OR exclusive_slot = 1", name="ck_infrastructure_operations_slot"),
        sa.CheckConstraint(
            "status IN ('accepted', 'running', 'stopping', 'succeeded', 'failed', 'cancelled', "
            "'interrupted', 'recovery_required')",
            name="ck_infrastructure_operations_status",
        ),
    )
    op.create_index(
        "ix_infrastructure_operations_idempotency", "infrastructure_operations", ["principal", "idempotency_key"]
    )
    op.create_index("ix_infrastructure_operations_accepted_at", "infrastructure_operations", ["accepted_at"])
    op.add_column(
        "terraform_runs",
        sa.Column(
            "operation_id",
            sa.BigInteger,
            sa.ForeignKey("infrastructure_operations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("terraform_runs", "operation_id")
    op.drop_index("ix_infrastructure_operations_accepted_at", table_name="infrastructure_operations")
    op.drop_index("ix_infrastructure_operations_idempotency", table_name="infrastructure_operations")
    op.drop_table("infrastructure_operations")
