"""plan_8_1 section 1.4, decision D6: recover the studies whose read failed before bioAF could say so.

Every ``classified`` study that the failed-read rule matches (a blocked paper-reading issue and no
claims, or a failed extraction cycle) moves to ``error``, audited, with its prior state, classification
and failure reason kept in ``evidence_json.recovery_history``. Retry then reads it again. The rule is
``app.services.validation_read_failure.read_failure``, the one every report surface renders with.

Revision ID: 142
Revises: 141
"""

from alembic import op

from app.services.validation_read_recovery import recover_failed_reads

revision = "142"
down_revision = "141"
branch_labels = None
depends_on = None


def upgrade() -> None:
    recover_failed_reads(op.get_bind())


def downgrade() -> None:
    # A data recovery: the prior state of each study it moved is kept in its recovery history and audit
    # entry, and restoring a manufactured classification is not something to automate.
    pass
