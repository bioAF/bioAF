"""Update default interactive machine type from n2-standard-4 to e2-standard-8.

The interactive node pool was defaulting to n2-standard-4, which suffered two
problems on fresh installs:

1. n2 is the deprioritized Intel Cascade Lake family. GCP allocates new
   capacity to c3/n4/c4/n2d/c2d first; n2 gets residual capacity. We saw
   repeated GCE-out-of-resources failures on scale-ups in us-central1-a.
2. n2-standard-4 (4 vCPU / 16 GB) only supports Small notebooks, so new
   installs could not launch a Medium notebook until the operator manually
   changed the cluster config.

e2-standard-8 (8 vCPU / 32 GB) fixes both. The e2 family spills onto any
available host generation so it almost never stocks out, and the larger
shape unlocks Medium notebooks out of the box.

This migration only updates rows whose value is still the prior default
(n2-standard-4); rows already customized by the operator are left alone.
Mirrors the pattern in migration 055 for the pipeline pool default.

Revision ID: 093
Revises: 092
"""

from alembic import op

revision = "093"
down_revision = "092"


def upgrade() -> None:
    op.execute(
        "UPDATE platform_config "
        "SET value = 'e2-standard-8', updated_at = now() "
        "WHERE key = 'k8s_interactive_machine_type' AND value = 'n2-standard-4'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE platform_config "
        "SET value = 'n2-standard-4', updated_at = now() "
        "WHERE key = 'k8s_interactive_machine_type' AND value = 'e2-standard-8'"
    )
