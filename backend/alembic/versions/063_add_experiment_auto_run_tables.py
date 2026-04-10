"""Add experiment_auto_runs and pending_auto_runs tables.

Revision ID: 063
Revises: 062

Supports automatic pipeline execution triggered by sample completeness
after manifest-driven file ingest. Configuration is per-experiment;
pending runs are queued per-sample and launched by a background loop.
"""

from alembic import op
import sqlalchemy as sa

revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_auto_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("experiment_id", sa.Integer(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("pipeline_key", sa.String(255), nullable=False),
        sa.Column("parameters_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("reference_genome", sa.String(200), nullable=True),
        sa.Column("alignment_algorithm", sa.String(200), nullable=True),
        sa.Column("delay_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("configured_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_experiment_auto_runs_experiment_id", "experiment_auto_runs", ["experiment_id"])
    op.create_index("ix_experiment_auto_runs_org_enabled", "experiment_auto_runs", ["organization_id", "enabled"])

    op.create_table(
        "pending_auto_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "auto_run_config_id",
            sa.Integer(),
            sa.ForeignKey("experiment_auto_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("experiment_id", sa.Integer(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("sample_id", sa.Integer(), sa.ForeignKey("samples.id"), nullable=False),
        sa.Column("sample_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="waiting"),
        sa.Column("pipeline_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=True),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pending_auto_run_status_scheduled", "pending_auto_runs", ["status", "scheduled_at"])
    op.create_unique_constraint(
        "uq_pending_auto_run_config_sample", "pending_auto_runs", ["auto_run_config_id", "sample_id"]
    )


def downgrade() -> None:
    op.drop_table("pending_auto_runs")
    op.drop_table("experiment_auto_runs")
