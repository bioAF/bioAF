"""The notebook-image Artifact Registry repo is provisioned imperatively by
notebook_image_service.ensure_artifact_registry, not by Terraform.

The old top-level terraform/notebooks.tf root config was dead (never shipped: the
backend image copies only backend/terraform/modules) and was removed in ADR-066.
These tests assert the real provisioning path instead of the deleted .tf files.
"""

import inspect

from app.services import notebook_image_service as nis


def test_artifact_registry_repo_is_docker_format():
    """The image registry creates the bioaf-images repo as a DOCKER repo.

    The Artifact Registry REST moved behind the ImageRegistry seam in Stage 4e;
    ensure_artifact_registry now delegates to GcpArtifactRegistryProvider, so the
    DOCKER-format contract is asserted against the provider.
    """
    from app.adapters.image_registry import gcp as ar_gcp

    assert nis.AR_REPO_ID == "bioaf-images"
    assert ar_gcp.AR_REPO_ID == "bioaf-images"
    src = inspect.getsource(ar_gcp)
    assert "artifactregistry.googleapis.com" in src
    assert "repositoryId=" in src
    assert '"format": "DOCKER"' in src


def test_artifact_registry_repo_recorded_in_config():
    """The image build records the repo path under the artifact_registry_repo
    platform_config key (replacing the old terraform outputs.tf output) and
    ensures the repo exists before building."""
    src = inspect.getsource(nis.build_notebook_image)
    assert "artifact_registry_repo" in src
    assert "ensure_image_repository" in src
