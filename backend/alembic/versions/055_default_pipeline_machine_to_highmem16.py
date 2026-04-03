"""Update default pipeline machine type from n2-highmem-8 to n2-highmem-16.

n2-highmem-8 is insufficient for running Nextflow pipelines like
nf-core/scrnaseq. Existing deployments that still have the original
seed value are updated to n2-highmem-16.

Revision ID: 055
Revises: 054
"""

from alembic import op

revision = "055"
down_revision = "054"


def upgrade() -> None:
    op.execute(
        "UPDATE platform_config "
        "SET value = 'n2-highmem-16', updated_at = now() "
        "WHERE key = 'k8s_pipeline_machine_type' AND value = 'n2-highmem-8'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE platform_config "
        "SET value = 'n2-highmem-8', updated_at = now() "
        "WHERE key = 'k8s_pipeline_machine_type' AND value = 'n2-highmem-16'"
    )
