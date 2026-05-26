"""Seed default platform_config rows for networking settings.

Revision ID: 090
Revises: 089
Create Date: 2026-05-26

Adds the seven networking config keys (hostname, domain, reachability status
+ checked-at, cert status, https_enforced) with empty defaults. The Settings
-> Networking page reads and writes these via /api/v1/settings/networking.
"""

import sqlalchemy as sa
from alembic import op

revision = "090"
down_revision = "089"
branch_labels = None
depends_on = None


_DEFAULTS = [
    ("networking_hostname", ""),
    ("networking_domain", ""),
    ("networking_reachability_status", ""),
    ("networking_reachability_checked_at", ""),
    ("networking_cert_status", ""),
    ("networking_https_enforced", "false"),
]


def upgrade() -> None:
    platform_config = sa.table(
        "platform_config",
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
    )
    op.bulk_insert(platform_config, [{"key": k, "value": v} for k, v in _DEFAULTS])


def downgrade() -> None:
    op.execute(
        "DELETE FROM platform_config WHERE key IN ("
        "'networking_hostname',"
        "'networking_domain',"
        "'networking_reachability_status',"
        "'networking_reachability_checked_at',"
        "'networking_cert_status',"
        "'networking_https_enforced'"
        ")"
    )
