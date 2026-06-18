"""Unit tests for the ImageRegistry seam (Stage 4e).

DB-free: the GCP (Artifact Registry) provider's image-URI construction and the
idempotent repository ensure (GET-then-create), plus the factory defaulting to
Artifact Registry on a GCP/unconfigured install.
"""

from unittest.mock import patch

import pytest

from app.adapters.image_registry import (
    DEFAULT_IMAGE_REGISTRY_BACKEND,
    VALID_IMAGE_REGISTRY_BACKENDS,
    create_image_registry_provider,
    get_image_registry_provider,
)
from app.adapters.image_registry.gcp import GcpArtifactRegistryProvider

CONFIG = {"project_id": "my-project", "region": "us-central1"}


def test_image_uri_is_artifact_registry_format():
    p = GcpArtifactRegistryProvider()
    assert p.image_uri(CONFIG, "bioaf-scrna", "latest") == (
        "us-central1-docker.pkg.dev/my-project/bioaf-images/bioaf-scrna:latest"
    )


def test_ensure_repository_returns_existing_without_creating():
    p = GcpArtifactRegistryProvider()
    with patch("app.adapters.image_registry.gcp.authorized_request") as req:
        req.return_value = {}  # GET succeeds -> repo exists
        repo = p.ensure_repository(object(), CONFIG, "bioaf-scrna")
    assert repo == "projects/my-project/locations/us-central1/repositories/bioaf-images"
    # Only the existence GET, no create POST.
    assert req.call_count == 1
    assert req.call_args.args[1] == "GET"


def test_ensure_repository_creates_when_absent():
    p = GcpArtifactRegistryProvider()

    def fake(credentials, method, url, body=None):
        if method == "GET":
            raise RuntimeError("404 not found")
        return {}

    with patch("app.adapters.image_registry.gcp.authorized_request", side_effect=fake) as req:
        p.ensure_repository(object(), CONFIG, "bioaf-scrna")
    methods = [c.args[1] for c in req.call_args_list]
    assert methods == ["GET", "POST"]
    # The create POST carries a DOCKER-format body.
    create_call = req.call_args_list[1]
    assert create_call.args[3]["format"] == "DOCKER"
    assert "repositoryId=bioaf-images" in create_call.args[2]


def test_factory_defaults_to_artifact_registry():
    assert DEFAULT_IMAGE_REGISTRY_BACKEND == "artifact_registry"
    assert "artifact_registry" in VALID_IMAGE_REGISTRY_BACKENDS
    assert isinstance(create_image_registry_provider("artifact_registry"), GcpArtifactRegistryProvider)


def test_get_image_registry_provider_falls_back_when_cache_unloaded():
    from app.platform.cloud_provider import reset_resolved_backends

    reset_resolved_backends()
    assert isinstance(get_image_registry_provider(), GcpArtifactRegistryProvider)


def test_unknown_backend_raises():
    from app.exceptions import ValidationError

    with pytest.raises(ValidationError):
        create_image_registry_provider("quay")  # not a supported registry backend


# --- ECR (Stage 6e AWS realization) -------------------------------------------

AWS_CONFIG = {"account_id": "043671579834", "region": "us-west-1"}


def test_ecr_image_uri_is_one_repo_per_image():
    from app.adapters.image_registry.aws import EcrImageRegistryProvider

    p = EcrImageRegistryProvider()
    # ECR: repo == image name (one repo per image), account+region host.
    assert p.image_uri(AWS_CONFIG, "bioaf-cellxgene", "latest") == (
        "043671579834.dkr.ecr.us-west-1.amazonaws.com/bioaf-cellxgene:latest"
    )


def test_ecr_ensure_repository_creates_when_absent():
    from app.adapters.image_registry.aws import EcrImageRegistryProvider

    p = EcrImageRegistryProvider()
    with patch.object(EcrImageRegistryProvider, "_client") as mk:
        ecr = mk.return_value
        repo = p.ensure_repository(None, AWS_CONFIG, "bioaf-cellxgene")
    assert repo == "bioaf-cellxgene"
    ecr.create_repository.assert_called_once_with(repositoryName="bioaf-cellxgene")


def test_ecr_ensure_repository_idempotent_when_already_exists():
    from botocore.exceptions import ClientError

    from app.adapters.image_registry.aws import EcrImageRegistryProvider

    p = EcrImageRegistryProvider()
    err = ClientError(
        {"Error": {"Code": "RepositoryAlreadyExistsException", "Message": "exists"}},
        "CreateRepository",
    )
    with patch.object(EcrImageRegistryProvider, "_client") as mk:
        mk.return_value.create_repository.side_effect = err
        repo = p.ensure_repository(None, AWS_CONFIG, "bioaf-cellxgene")  # must not raise
    assert repo == "bioaf-cellxgene"


def test_ecr_ensure_repository_propagates_other_errors():
    from botocore.exceptions import ClientError

    from app.adapters.image_registry.aws import EcrImageRegistryProvider

    p = EcrImageRegistryProvider()
    err = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
        "CreateRepository",
    )
    with patch.object(EcrImageRegistryProvider, "_client") as mk:
        mk.return_value.create_repository.side_effect = err
        with pytest.raises(ClientError):
            p.ensure_repository(None, AWS_CONFIG, "bioaf-cellxgene")


def test_factory_builds_ecr_when_selected():
    from app.adapters.image_registry.aws import EcrImageRegistryProvider

    assert "ecr" in VALID_IMAGE_REGISTRY_BACKENDS
    assert isinstance(create_image_registry_provider("ecr"), EcrImageRegistryProvider)
