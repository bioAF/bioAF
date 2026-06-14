"""Stack options: the valid (compute_stack, storage) combos for a cloud (stage 8).

The single source of truth the ``/stack-options`` endpoint and the frontend share.
It combines the user-selectable compute_stack (a workload choice, not cloud-derived)
with the cloud-resolved object-store backend (from ``cloud_provider.POLICY``) and
cloud-specific display labels. SLURM stages on NFS regardless of cloud; it is a
valid compute_stack but not yet selectable, matching the UI's "coming soon".

Behavior-preserving on GCP: kubernetes -> "Kubernetes + GCS" (GKE, recommended),
slurm -> "SLURM + NFS" (unavailable), exactly today's setup UI. On AWS the same
shapes resolve to EKS + S3.
"""

from __future__ import annotations

from app.platform.cloud_provider import POLICY, InvalidBackendError
from app.schemas.infrastructure import StackOption

# Cloud-specific display names for the managed-Kubernetes and object-store backends.
# The POLICY owns backend *selection*; this is the thin presentation layer on top.
_CLOUD_LABELS: dict[str, dict[str, str]] = {
    "gcp": {"managed_k8s": "GKE", "object_store": "GCS"},
    "aws": {"managed_k8s": "EKS", "object_store": "S3"},
}


def stack_options_for(cloud_provider: str) -> list[StackOption]:
    """Return the selectable stack options for ``cloud_provider`` (fails closed)."""
    if cloud_provider not in POLICY or cloud_provider not in _CLOUD_LABELS:
        raise InvalidBackendError(f"Unknown cloud_provider '{cloud_provider}'. Supported: {sorted(_CLOUD_LABELS)}.")

    object_store = POLICY[cloud_provider]["storage"]  # gcs | s3
    labels = _CLOUD_LABELS[cloud_provider]

    return [
        StackOption(
            compute_stack="kubernetes",
            storage_backend=object_store,
            label=f"Kubernetes + {labels['object_store']}",
            compute_label=f"Kubernetes ({labels['managed_k8s']})",
            storage_label=labels["object_store"],
            available=True,
            recommended=True,
        ),
        StackOption(
            compute_stack="slurm",
            storage_backend="nfs",
            label="SLURM + NFS",
            compute_label="SLURM",
            storage_label="NFS",
            available=False,
            recommended=False,
        ),
    ]
