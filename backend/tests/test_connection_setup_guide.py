"""Tests for the cloud-selected connection setup guide (Leak 2 drain, Stage 3d.2).

The kubectl-access guide (gcloud on GKE) moved off the ssh_connect endpoint and
behind the compute/notebook adapters, so the service layer names no cloud CLI.
Pure, no DB.
"""

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.compute.slurm import SlurmComputeProvider
from app.adapters.kubernetes.connection import KUBECTL_SETUP_GUIDE
from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider


def test_kubernetes_compute_returns_gke_setup_guide():
    guide = KubernetesComputeProvider().connection_setup_guide()
    assert guide == KUBECTL_SETUP_GUIDE
    assert "kubectl" in guide


def test_kubernetes_notebook_returns_gke_setup_guide():
    assert KubernetesNotebookProvider().connection_setup_guide() == KUBECTL_SETUP_GUIDE


def test_default_guide_is_slurm_ssh_guidance():
    # The ABC default (used by the SLURM backend) names no cloud CLI.
    guide = SlurmComputeProvider().connection_setup_guide()
    assert "SLURM" in guide
    assert guide != ""
