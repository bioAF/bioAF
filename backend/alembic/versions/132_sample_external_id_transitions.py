"""Record of samples whose external ID migration 080 assigned (plan 9, owner policy 2026-11-07).

Revision ID: 132
Revises: 131
Create Date: 2026-11-07

Migration 080 now creates sample_external_id_transitions when it relabels duplicate legacy source
labels. Installs that were already past 080 never ran that version of 080, so they get the same
(empty) table here; on every other install it already exists and this does nothing. Same DDL as
080's, copied rather than imported so neither migration depends on the other's file.
"""

from alembic import op

revision = "132"
down_revision = "131"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sample_external_id_transitions (
            id SERIAL PRIMARY KEY,
            sample_id INTEGER NOT NULL UNIQUE REFERENCES samples (id) ON DELETE CASCADE,
            experiment_id INTEGER NOT NULL REFERENCES experiments (id),
            source_label VARCHAR(255) NOT NULL,
            external_id VARCHAR(255) NOT NULL,
            revision VARCHAR(32) NOT NULL DEFAULT '080',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sample_external_id_transitions_label "
        "ON sample_external_id_transitions (experiment_id, source_label)"
    )


def downgrade() -> None:
    # The table belongs to 080 on installs that crossed it; 080's downgrade drops it.
    pass
