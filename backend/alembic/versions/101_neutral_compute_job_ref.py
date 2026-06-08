"""Neutral compute_job_ref + provider_metadata columns (BAL rework, Phase 4).

pipeline_runs and compute_sessions (notebook sessions) carry Kubernetes-named
columns (k8s_job_name / k8s_namespace / k8s_pod_name) that a non-Kubernetes
compute backend (SLURM) cannot honestly populate. This migration adds
backend-neutral columns:

  - ``compute_job_ref``: the opaque job/session handle the active compute
    adapter round-trips (the K8s job name for runs; the pod name for sessions).
  - ``provider_metadata``: backend-specific detail for the provider-details UI
    disclosure, e.g. ``{"kubernetes": {"job_name", "namespace", "pod_name"}}``.

It backfills the new columns from the existing k8s_* values and leaves the old
columns in place. A later migration drops the k8s_* columns once every caller
reads/writes the neutral columns, so this step is fully reversible.

Revision ID: 101
Revises: 100
Create Date: 2026-06-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "101"
down_revision = "100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("pipeline_runs", "compute_sessions"):
        op.add_column(table, sa.Column("compute_job_ref", sa.String(length=255), nullable=True))
        op.add_column(table, sa.Column("provider_metadata", JSONB(), nullable=True))

    # Backfill pipeline_runs: job handle = k8s_job_name; metadata = the K8s
    # specifics as a flat backend-detail dict (the UI disclosure renders it
    # generically; the backend label comes from the active-stack info).
    op.execute(
        "UPDATE pipeline_runs SET "
        "compute_job_ref = k8s_job_name, "
        "provider_metadata = jsonb_strip_nulls(jsonb_build_object("
        "'job_name', k8s_job_name, 'namespace', k8s_namespace, 'pod_name', k8s_pod_name)) "
        "WHERE k8s_job_name IS NOT NULL OR k8s_namespace IS NOT NULL OR k8s_pod_name IS NOT NULL"
    )

    # Backfill compute_sessions: handle = k8s_pod_name; metadata = pod + namespace.
    op.execute(
        "UPDATE compute_sessions SET "
        "compute_job_ref = k8s_pod_name, "
        "provider_metadata = jsonb_strip_nulls(jsonb_build_object("
        "'pod_name', k8s_pod_name, 'namespace', k8s_namespace)) "
        "WHERE k8s_pod_name IS NOT NULL OR k8s_namespace IS NOT NULL"
    )


def downgrade() -> None:
    for table in ("pipeline_runs", "compute_sessions"):
        op.drop_column(table, "provider_metadata")
        op.drop_column(table, "compute_job_ref")
