"""Widen lab_glossary_scan_jobs.scan_type CHECK to allow 'experiment'.

LK-SPEC-D (D1): the ``topic`` glossary scan source is replaced by ``experiment``.
This is a widen-only change (LK-SPEC-D, OQ-4): ``experiment`` is added to the
allowed set and ``topic`` is kept so historical ``topic`` rows remain valid. New
``topic`` jobs are rejected at the service layer, not the database.

Revision ID: 100
Revises: 099
"""

from alembic import op

revision = "100"
down_revision = "099"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_lab_glossary_scan_jobs_type"
_TABLE = "lab_glossary_scan_jobs"

_NEW = "scan_type IN ('experiment', 'document', 'topic', 'platform_wide', 'import')"
_OLD = "scan_type IN ('document', 'topic', 'platform_wide', 'import')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD)
