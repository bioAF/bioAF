"""Infrastructure definition baselines and the post-update notice.

Revision ID: 141
Revises: 140
Create Date: 2026-11-21

Adds the per-module record of which infrastructure definitions were last fully reconciled, the
notice shown to administrators when the installed release's definitions differ, and per-admin
acknowledgments. Empty: an existing installation starts with no baseline (reported as unknown,
never assumed current). No Terraform runs and nothing in the cloud changes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "141"
down_revision = "140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "infrastructure_definition_baselines",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cloud", sa.String(16), nullable=False),
        sa.Column("module", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("destructive_pending", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "operation_id",
            sa.BigInteger,
            sa.ForeignKey("infrastructure_operations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cloud", "module", name="uq_infrastructure_definition_baselines_module"),
    )
    op.create_table(
        "infrastructure_definition_notices",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("release_version", sa.String(64), nullable=False),
        sa.Column("pending_modules", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("unknown_baseline", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "infrastructure_notice_acknowledgments",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "notice_id",
            sa.BigInteger,
            sa.ForeignKey("infrastructure_definition_notices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("notice_id", "user_id", name="uq_infrastructure_notice_ack_user"),
    )


def downgrade() -> None:
    op.drop_table("infrastructure_notice_acknowledgments")
    op.drop_table("infrastructure_definition_notices")
    op.drop_table("infrastructure_definition_baselines")
