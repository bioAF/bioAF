"""Session failure taxonomy + requested disk size.

Adds three columns to compute_sessions so the UI can:

1. Classify a failed session beyond "failed" -- e.g. show "Resource Failure"
   when the underlying GCE / GKE pool returned ZONE_RESOURCE_POOL_EXHAUSTED.
2. Surface a human-readable failure_message in the detail modal so the user
   knows exactly why the launch failed.
3. Show the requested boot disk size in the detail modal alongside CPU/RAM.

All three columns are nullable so the migration is non-blocking for existing
sessions. The adapters (K8s notebook adapter and GCE work node adapter)
populate failure_reason / failure_message when they detect a known failure
mode. requested_disk_gb is populated at launch from the platform_config
boot disk for the relevant pool / VM.

Revision ID: 094
Revises: 093
"""

import sqlalchemy as sa
from alembic import op

revision = "094"
down_revision = "093"


def upgrade() -> None:
    op.add_column(
        "compute_sessions",
        sa.Column("failure_reason", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "compute_sessions",
        sa.Column("failure_message", sa.String(), nullable=True),
    )
    op.add_column(
        "compute_sessions",
        sa.Column("requested_disk_gb", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("compute_sessions", "requested_disk_gb")
    op.drop_column("compute_sessions", "failure_message")
    op.drop_column("compute_sessions", "failure_reason")
