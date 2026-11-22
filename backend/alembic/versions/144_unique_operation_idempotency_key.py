"""One infrastructure operation per principal and Idempotency-Key.

Revision ID: 144
Revises: 143
Create Date: 2026-11-22

Overlapping requests with the same key could each find no earlier operation and insert one. A unique
index lets the database decide: the loser replays the winner's operation. Duplicates recorded before
this keep the key on their newest operation only; older ones keep their history without the key.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "144"
down_revision = "143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE infrastructure_operations o SET idempotency_key = NULL "
            "WHERE idempotency_key IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM infrastructure_operations n "
            "  WHERE n.principal = o.principal AND n.idempotency_key = o.idempotency_key AND n.id > o.id)"
        )
    )
    op.drop_index("ix_infrastructure_operations_idempotency", table_name="infrastructure_operations")
    op.create_index(
        "uq_infrastructure_operations_idempotency",
        "infrastructure_operations",
        ["principal", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_infrastructure_operations_idempotency", table_name="infrastructure_operations")
    op.create_index(
        "ix_infrastructure_operations_idempotency", "infrastructure_operations", ["principal", "idempotency_key"]
    )
