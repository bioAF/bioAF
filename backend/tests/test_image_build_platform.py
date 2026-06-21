"""DB-free unit tests for the cloud-neutral image-platform resolver (Stage 6e).

Patches ``get_cloud_provider`` + ``PlatformConfigService.get_many`` so the
resolution policy is exercised without a database. Pins that GCP resolves the
same project/region/credentials/build-SA it did pre-6e (byte-identical) and that
AWS resolves account/region/codebuild-project with ambient creds + no build SA.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import ValidationError
from app.services.image_build_platform import (
    ImagePlatform,
    resolve_image_credentials,
    resolve_image_platform,
)


@pytest.mark.asyncio
async def test_resolve_gcp_is_byte_identical_shape():
    cfg = {
        "gcp_project_id": "test-project",
        "gcp_region": "us-central1",
        "gcp_service_account_email": "runner@test-project.iam.gserviceaccount.com",
    }
    with (
        patch("app.services.image_build_platform.get_cloud_provider", AsyncMock(return_value="gcp")),
        patch(
            "app.services.image_build_platform.PlatformConfigService.get_many",
            AsyncMock(return_value=cfg),
        ),
    ):
        p = await resolve_image_platform(object())
    assert p.cloud_provider == "gcp"
    assert p.config == {"project_id": "test-project", "region": "us-central1"}
    assert p.build_sa == "runner@test-project.iam.gserviceaccount.com"
    assert p.has_target is True
    p.require_target()  # no raise
    p.require_build_service()  # Cloud Build is serverless -> no raise


@pytest.mark.asyncio
async def test_resolve_gcp_build_sa_is_none_when_unset_for_submit_fallback():
    # When gcp_service_account_email is unset, the platform carries None and the
    # submit path falls back to the credentials' own SA (tested at the service).
    cfg = {"gcp_project_id": "p", "gcp_region": "r", "gcp_service_account_email": "null"}
    with (
        patch("app.services.image_build_platform.get_cloud_provider", AsyncMock(return_value="gcp")),
        patch(
            "app.services.image_build_platform.PlatformConfigService.get_many",
            AsyncMock(return_value=cfg),
        ),
    ):
        p = await resolve_image_platform(object())
    assert p.build_sa is None


@pytest.mark.asyncio
async def test_resolve_aws_uses_account_region_project():
    cfg = {
        "aws_account_id": "043671579834",
        "aws_region": "us-west-1",
        "aws_codebuild_project": "bioaf-image-build",
    }
    with (
        patch("app.services.image_build_platform.get_cloud_provider", AsyncMock(return_value="aws")),
        patch(
            "app.services.image_build_platform.PlatformConfigService.get_many",
            AsyncMock(return_value=cfg),
        ),
    ):
        p = await resolve_image_platform(object())
    assert p.cloud_provider == "aws"
    assert p.config == {
        "account_id": "043671579834",
        "region": "us-west-1",
        "codebuild_project": "bioaf-image-build",
    }
    assert p.build_sa is None  # CodeBuild's own service role
    p.require_target()
    p.require_build_service()


@pytest.mark.asyncio
async def test_resolve_credentials_is_none_on_aws_and_loads_on_gcp():
    aws = ImagePlatform("aws", {"account_id": "1", "region": "us-west-1", "codebuild_project": "x"}, None)
    assert await resolve_image_credentials(object(), aws) is None  # ambient boto3; no fetch

    gcp = ImagePlatform("gcp", {"project_id": "p", "region": "r"}, None)
    sentinel = object()
    with patch("app.services.notebook_image_service._get_credentials", AsyncMock(return_value=sentinel)):
        assert await resolve_image_credentials(object(), gcp) is sentinel


@pytest.mark.asyncio
async def test_resolve_aws_raises_when_build_project_absent():
    cfg = {"aws_account_id": "1", "aws_region": "us-west-1", "aws_codebuild_project": "null"}
    with (
        patch("app.services.image_build_platform.get_cloud_provider", AsyncMock(return_value="aws")),
        patch(
            "app.services.image_build_platform.PlatformConfigService.get_many",
            AsyncMock(return_value=cfg),
        ),
    ):
        p = await resolve_image_platform(object())
    assert p.has_target is True
    p.require_target()  # account+region present
    with pytest.raises(ValidationError):
        p.require_build_service()  # but the CodeBuild project is not deployed


def test_require_target_messages_are_cloud_specific():
    gcp = ImagePlatform("gcp", {"project_id": "", "region": ""}, None)
    with pytest.raises(ValidationError, match="GCP project not configured"):
        gcp.require_target()
    aws = ImagePlatform("aws", {"account_id": "", "region": ""}, None)
    with pytest.raises(ValidationError, match="AWS account not configured"):
        aws.require_target()
    assert gcp.has_target is False
    assert aws.has_target is False
