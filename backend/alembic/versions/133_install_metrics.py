"""Installation metrics: the report outbox and the fresh-versus-existing classification (ADR-075).

Revision ID: 133
Revises: 132
Create Date: 2026-11-15

The classification is made here, before bootstrap can create an Organization: a database with an
Organization (and so possibly an admin) is an existing installation, and one whose setup is complete is
marked so. The admin's choice stays pending and nothing is reported or sent. A state the installation
already has is never replaced. The state's shape is copied from app/services/install_metrics/state.py
(schema 1) rather than imported, so this migration keeps its meaning if that code changes.
"""

from alembic import op

revision = "133"
down_revision = "132"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS install_metric_events (
            id BIGSERIAL PRIMARY KEY,
            source_key VARCHAR(128) NOT NULL UNIQUE,
            event_uuid UUID NOT NULL UNIQUE,
            event_name VARCHAR(64) NOT NULL,
            payload JSONB,
            report_date DATE NOT NULL,
            status VARCHAR(16) NOT NULL,
            attempts INTEGER NOT NULL,
            next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
            last_error VARCHAR(32),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            settled_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_install_metric_events_status ON install_metric_events (status)")
    op.execute(
        """
        INSERT INTO platform_config (key, value)
        SELECT 'install_metrics_state', jsonb_build_object(
            'schema', 1,
            'installation_id', NULL,
            'classification', CASE WHEN EXISTS (SELECT 1 FROM organizations) THEN 'existing' ELSE 'new' END,
            'preference', 'pending',
            'notice_version', NULL,
            'decided_at', NULL,
            'enabled_since', NULL,
            'override_active', false,
            'setup_completed', EXISTS (SELECT 1 FROM organizations WHERE setup_complete),
            'baseline', NULL,
            'revision', 0
        )::text
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM platform_config WHERE key = 'install_metrics_state'")
    op.execute("DROP TABLE IF EXISTS install_metric_events")
