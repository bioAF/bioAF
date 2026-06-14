"""Unit tests for the ImageBuild seam (Stage 4e).

DB-free: the GCP (Cloud Build) provider's submit / status / cancel REST shaping
and the neutral status contract, plus the factory defaulting to Cloud Build on a
GCP/unconfigured install.
"""

from unittest.mock import patch

import pytest

from app.adapters.image_build import (
    BUILD_STATUSES,
    DEFAULT_IMAGE_BUILD_BACKEND,
    VALID_IMAGE_BUILD_BACKENDS,
    create_image_build_provider,
    get_image_build_provider,
)
from app.adapters.image_build.gcp import GcpCloudBuildProvider

CONFIG = {"project_id": "my-project", "region": "us-central1"}


def test_neutral_status_contract():
    assert BUILD_STATUSES == frozenset({"QUEUED", "WORKING", "SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"})


def test_submit_build_shapes_cloud_build_body_and_returns_id():
    p = GcpCloudBuildProvider()
    with patch("app.adapters.image_build.gcp.authorized_request") as req:
        req.return_value = {"metadata": {"build": {"id": "build-123"}}}
        build_id = p.submit_build(
            object(),
            CONFIG,
            context_object_uri="gs://work-bucket/builds/bioaf-scrna/source.tar.gz",
            image_uri="us-central1-docker.pkg.dev/my-project/bioaf-images/bioaf-scrna:latest",
            build_sa="runner@my-project.iam.gserviceaccount.com",
            timeout="7200s",
        )
    assert build_id == "build-123"
    method, url, body = req.call_args.args[1], req.call_args.args[2], req.call_args.args[3]
    assert method == "POST"
    assert url == "https://cloudbuild.googleapis.com/v1/projects/my-project/builds"
    # storageSource parsed from the gs:// context URI.
    assert body["source"]["storageSource"] == {"bucket": "work-bucket", "object": "builds/bioaf-scrna/source.tar.gz"}
    assert body["images"] == ["us-central1-docker.pkg.dev/my-project/bioaf-images/bioaf-scrna:latest"]
    assert body["options"]["machineType"] == "E2_HIGHCPU_8"
    assert body["timeout"] == "7200s"
    # The build SA is wrapped to the Cloud Build resource form.
    assert body["serviceAccount"] == "projects/my-project/serviceAccounts/runner@my-project.iam.gserviceaccount.com"
    assert body["options"]["defaultLogsBucketBehavior"] == "REGIONAL_USER_OWNED_BUCKET"


def test_submit_build_without_sa_omits_service_account():
    p = GcpCloudBuildProvider()
    with patch("app.adapters.image_build.gcp.authorized_request") as req:
        req.return_value = {"metadata": {"build": {"id": "b"}}}
        p.submit_build(
            object(),
            CONFIG,
            context_object_uri="gs://b/o",
            image_uri="img:latest",
            build_sa=None,
            timeout="3600s",
        )
    body = req.call_args.args[3]
    assert "serviceAccount" not in body
    assert "defaultLogsBucketBehavior" not in body["options"]


def test_check_build_status_returns_status_or_unknown():
    p = GcpCloudBuildProvider()
    with patch("app.adapters.image_build.gcp.authorized_request") as req:
        req.return_value = {"status": "SUCCESS"}
        assert p.check_build_status(object(), CONFIG, "build-123") == "SUCCESS"
    with patch("app.adapters.image_build.gcp.authorized_request", side_effect=RuntimeError("boom")):
        assert p.check_build_status(object(), CONFIG, "build-123") == "UNKNOWN"


def test_cancel_build_posts_to_cancel_endpoint_and_swallows_errors():
    p = GcpCloudBuildProvider()
    with patch("app.adapters.image_build.gcp.authorized_request") as req:
        p.cancel_build(object(), CONFIG, "build-123")
    assert req.call_args.args[1] == "POST"
    assert req.call_args.args[2].endswith("/builds/build-123:cancel")
    # An error from the cancel API must not propagate (build may already be done).
    with patch("app.adapters.image_build.gcp.authorized_request", side_effect=RuntimeError("already done")):
        p.cancel_build(object(), CONFIG, "build-123")  # no raise


def test_factory_defaults_to_cloud_build():
    assert DEFAULT_IMAGE_BUILD_BACKEND == "cloud_build"
    assert "cloud_build" in VALID_IMAGE_BUILD_BACKENDS
    assert isinstance(create_image_build_provider("cloud_build"), GcpCloudBuildProvider)


def test_get_image_build_provider_falls_back_when_cache_unloaded():
    from app.platform.cloud_provider import reset_resolved_backends

    reset_resolved_backends()
    assert isinstance(get_image_build_provider(), GcpCloudBuildProvider)


def test_unknown_backend_raises():
    from app.exceptions import ValidationError

    with pytest.raises(ValidationError):
        create_image_build_provider("codebuild")  # no CodeBuild impl until Stage 6e
