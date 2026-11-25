"""Durable work node sync credentials, server identity and truthful Stop state.

Revision ID: 147
Revises: 146
Create Date: 2026-11-24

New work nodes record an encrypted backend sync key, their launch host public key, the immutable
provider instance id and the launch attempt before the provider call. Rows launched earlier keep
NULL in every new column (node_trust_version NULL marks them as legacy). Stop bookkeeping columns
let a failed save keep the node instead of reporting it stopped.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "147"
down_revision = "146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compute_sessions", sa.Column("sync_private_key", sa.Text(), nullable=True))
    op.add_column("compute_sessions", sa.Column("host_public_key", sa.Text(), nullable=True))
    op.add_column("compute_sessions", sa.Column("provider_instance_id", sa.String(64), nullable=True))
    op.add_column("compute_sessions", sa.Column("launch_attempt_id", sa.String(64), nullable=True))
    op.add_column("compute_sessions", sa.Column("node_trust_version", sa.Integer(), nullable=True))
    op.add_column("compute_sessions", sa.Column("stop_failure", sa.String(30), nullable=True))
    op.add_column("compute_sessions", sa.Column("stop_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("compute_sessions", sa.Column("stop_claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in (
        "stop_claimed_at",
        "stop_attempts",
        "stop_failure",
        "node_trust_version",
        "launch_attempt_id",
        "provider_instance_id",
        "host_public_key",
        "sync_private_key",
    ):
        op.drop_column("compute_sessions", column)
