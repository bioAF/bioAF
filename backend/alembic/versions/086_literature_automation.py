"""Automated AI Lit Review cadence + run trigger + notification rule.

Revision ID: 086
Revises: 085
Create Date: 2026-05-19

Adds:
  - literature_review_runs.trigger ('manual' | 'scheduled') so automated cadence
    runs are distinguishable from on-demand ones.
  - organizations.lit_review_auto_enabled / lit_review_auto_cadence /
    lit_review_max_runs_per_tick: the cadence config edited on
    Settings > Integrations > LLMs.
  - a default in-app NotificationRule per existing org for the new
    'literature.auto_review_recommendations' event, with a NULL role_filter so
    every active user gets the per-user indicator when a cadence run adds papers.
"""

import sqlalchemy as sa
from alembic import op

revision = "086"
down_revision = "085"
branch_labels = None
depends_on = None


AUTO_REVIEW_EVENT = "literature.auto_review_recommendations"


def upgrade() -> None:
    op.add_column(
        "literature_review_runs",
        sa.Column(
            "trigger",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "lit_review_auto_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "lit_review_auto_cadence",
            sa.String(length=16),
            nullable=False,
            server_default="weekly",
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "lit_review_max_runs_per_tick",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )

    # Seed a default in-app rule for every existing org that lacks one. NULL
    # role_filter means the notification router delivers in-app to all active
    # users (the per-user indicator), without seeding any email/Slack rule.
    op.execute(
        sa.text(
            """
            INSERT INTO notification_rules
                (organization_id, event_type, channel, role_filter, mandatory, enabled, created_at)
            SELECT o.id, :event, 'in_app', NULL, false, true, now()
            FROM organizations o
            WHERE NOT EXISTS (
                SELECT 1 FROM notification_rules r
                WHERE r.organization_id = o.id
                  AND r.event_type = :event
                  AND r.channel = 'in_app'
            )
            """
        ).bindparams(event=AUTO_REVIEW_EVENT)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notification_rules WHERE event_type = :event").bindparams(event=AUTO_REVIEW_EVENT))
    op.drop_column("organizations", "lit_review_max_runs_per_tick")
    op.drop_column("organizations", "lit_review_auto_cadence")
    op.drop_column("organizations", "lit_review_auto_enabled")
    op.drop_column("literature_review_runs", "trigger")
