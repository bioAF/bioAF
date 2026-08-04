"""Phase 7: Operations, Notifications + Polish tables.

Revision ID: 004
Revises: 003
Create Date: 2026-03-06

New tables: notifications, notification_rules, notification_preferences,
            slack_webhooks, notification_delivery_log, upgrade_history,
            access_log, activity_feed, budget_config, cost_records
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # notifications table
    # ---------------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=20), server_default=sa.text("'info'"), nullable=False),
        sa.Column("read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_notifications_user_read", "notifications", ["user_id", "read"])
    op.create_index("idx_notifications_org_created", "notifications", ["organization_id", "created_at"])
    op.create_index("idx_notifications_event_type", "notifications", ["event_type"])

    # ---------------------------------------------------------------
    # notification_rules table
    # ---------------------------------------------------------------
    op.create_table(
        "notification_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("role_filter", sa.String(length=50), nullable=True),
        sa.Column("mandatory", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "event_type", "channel", name="uq_notification_rules_org_event_channel"),
    )

    # ---------------------------------------------------------------
    # notification_preferences table
    # ---------------------------------------------------------------
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "event_type", "channel", name="uq_notification_prefs_user_event_channel"),
    )

    # ---------------------------------------------------------------
    # slack_webhooks table
    # ---------------------------------------------------------------
    op.create_table(
        "slack_webhooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("webhook_url", sa.String(length=1000), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.Column("event_types_json", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---------------------------------------------------------------
    # notification_delivery_log table
    # ---------------------------------------------------------------
    op.create_table(
        "notification_delivery_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---------------------------------------------------------------
    # upgrade_history table
    # ---------------------------------------------------------------
    op.create_table(
        "upgrade_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("from_version", sa.String(length=50), nullable=False),
        sa.Column("to_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_by_user_id", sa.Integer(), nullable=False),
        sa.Column("terraform_plan_json", JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["started_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---------------------------------------------------------------
    # access_log table
    # ---------------------------------------------------------------
    op.create_table(
        "access_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("metadata_json", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_access_log_org_created", "access_log", ["organization_id", "created_at"])
    op.create_index("idx_access_log_user_created", "access_log", ["user_id", "created_at"])
    op.create_index("idx_access_log_resource_type", "access_log", ["resource_type"])

    # ---------------------------------------------------------------
    # activity_feed table
    # ---------------------------------------------------------------
    op.create_table(
        "activity_feed",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metadata_json", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_activity_feed_org_created", "activity_feed", ["organization_id", "created_at"])

    # ---------------------------------------------------------------
    # budget_config table
    # ---------------------------------------------------------------
    op.create_table(
        "budget_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("monthly_budget", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("threshold_50_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("threshold_80_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("threshold_100_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("scale_to_zero_on_100", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )

    # ---------------------------------------------------------------
    # cost_records table
    # ---------------------------------------------------------------
    op.create_table(
        "cost_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("component", sa.String(length=100), nullable=False),
        sa.Column("cost_amount", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("metadata_json", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cost_records_org_date", "cost_records", ["organization_id", "record_date"])

    # Grant permissions (conditionally: bioaf_app role may not exist in dev/POC)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bioaf_app') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON notifications, notification_rules, notification_preferences, slack_webhooks, notification_delivery_log, upgrade_history, access_log, activity_feed, budget_config, cost_records TO bioaf_app';
                EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bioaf_app';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.drop_index("idx_cost_records_org_date")
    op.drop_table("cost_records")

    op.drop_table("budget_config")

    op.drop_index("idx_activity_feed_org_created")
    op.drop_table("activity_feed")

    op.drop_index("idx_access_log_resource_type")
    op.drop_index("idx_access_log_user_created")
    op.drop_index("idx_access_log_org_created")
    op.drop_table("access_log")

    op.drop_table("upgrade_history")

    op.drop_table("notification_delivery_log")

    op.drop_table("slack_webhooks")

    op.drop_table("notification_preferences")

    op.drop_table("notification_rules")

    op.drop_index("idx_notifications_event_type")
    op.drop_index("idx_notifications_org_created")
    op.drop_index("idx_notifications_user_read")
    op.drop_table("notifications")
