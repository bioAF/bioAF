"""Pure unit tests for the stack-options source of truth (stage 8).

Backend-only, no DB: exercises ``stack_options_for`` directly. The /stack-options
endpoint integration is covered in the infrastructure API tests.
"""

import pytest

from app.platform.cloud_provider import InvalidBackendError
from app.platform.stack_options import stack_options_for


def test_gcp_options_are_behavior_preserving():
    """GCP: Kubernetes + GCS (GKE, recommended, available) and SLURM + NFS
    (coming soon) - exactly what the setup UI shows today."""
    options = stack_options_for("gcp")
    by_stack = {o.compute_stack: o for o in options}

    k8s = by_stack["kubernetes"]
    assert k8s.storage_backend == "gcs"
    assert k8s.label == "Kubernetes + GCS"
    assert k8s.compute_label == "Kubernetes (GKE)"
    assert k8s.storage_label == "GCS"
    assert k8s.available is True
    assert k8s.recommended is True

    slurm = by_stack["slurm"]
    assert slurm.storage_backend == "nfs"
    assert slurm.label == "SLURM + NFS"
    assert slurm.available is False
    assert slurm.recommended is False


def test_aws_options_resolve_to_eks_and_s3():
    """AWS: the same shapes resolve to EKS + S3; SLURM + NFS stays cloud-neutral."""
    options = stack_options_for("aws")
    by_stack = {o.compute_stack: o for o in options}

    k8s = by_stack["kubernetes"]
    assert k8s.storage_backend == "s3"
    assert k8s.label == "Kubernetes + S3"
    assert k8s.compute_label == "Kubernetes (EKS)"
    assert k8s.storage_label == "S3"
    assert k8s.available is True

    slurm = by_stack["slurm"]
    assert slurm.storage_backend == "nfs"
    assert slurm.label == "SLURM + NFS"


def test_unknown_cloud_fails_closed():
    with pytest.raises(InvalidBackendError):
        stack_options_for("azure")
