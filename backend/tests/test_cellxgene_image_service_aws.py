"""AWS-path integration test for the cellxgene image build (Stage 6e).

Proves the cloud-neutral service wiring routes an ``aws`` install through the
ECR + CodeBuild providers: ``build_cellxgene_image`` ensures the per-image ECR
repo (``bioaf-cellxgene``) and submits a CodeBuild build whose image URI is the
ECR host. boto3 is mocked at the provider ``_client`` seam; the storage upload is
stubbed, so no real AWS calls are made. DB-bound (platform_config + the resolved-
backend cache), so CI-only locally per the repo conventions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

import app.platform.cloud_provider as cp
from app.services.cellxgene_image_service import build_cellxgene_image


@pytest_asyncio.fixture
async def aws_install(session):
    """Seed an AWS install + load the resolved-backend cache (ecr / codebuild)."""
    for key, value in [
        ("cloud_provider", "aws"),
        ("aws_account_id", "043671579834"),
        ("aws_region", "us-west-1"),
        ("aws_codebuild_project", "bioaf-image-build"),
        ("working_bucket_name", "bioaf-working-abc123"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()
    await cp.load_resolved_backends(session)
    try:
        yield
    finally:
        cp.reset_resolved_backends()


@pytest.mark.asyncio
async def test_build_cellxgene_image_on_aws_ensures_ecr_and_submits_codebuild(session, aws_install):
    from app.adapters.image_build.aws import CodeBuildImageBuildProvider
    from app.adapters.image_registry.aws import EcrImageRegistryProvider

    storage = MagicMock()
    storage.build_uri.return_value = "s3://bioaf-working-abc123/builds/bioaf-cellxgene/source.tar.gz"
    storage.upload_file = AsyncMock()
    storage.staging_image.return_value = "amazon/aws-cli"

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=storage),
        patch.object(EcrImageRegistryProvider, "_client") as ecr_mk,
        patch.object(CodeBuildImageBuildProvider, "_client") as cb_mk,
    ):
        cb_mk.return_value.start_build.return_value = {"build": {"id": "bioaf-image-build:run-1"}}
        build_id = await build_cellxgene_image(session)

    assert build_id == "bioaf-image-build:run-1"
    # The per-image ECR repo is ensured (ECR is one repo per image).
    ecr_mk.return_value.create_repository.assert_called_once_with(repositoryName="bioaf-cellxgene")
    # The CodeBuild build targets the project with the ECR image URI.
    kwargs = cb_mk.return_value.start_build.call_args.kwargs
    assert kwargs["projectName"] == "bioaf-image-build"
    env = {e["name"]: e["value"] for e in kwargs["environmentVariablesOverride"]}
    assert env["IMAGE_URI"] == "043671579834.dkr.ecr.us-west-1.amazonaws.com/bioaf-cellxgene:latest"
    # Build tracking is recorded.
    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'cellxgene_image_build_id'"))
    ).fetchone()
    assert row[0] == "bioaf-image-build:run-1"


@pytest.mark.asyncio
async def test_build_cellxgene_image_on_aws_raises_without_codebuild_project(session):
    # AWS install with account+region but no deployed CodeBuild project.
    for key, value in [
        ("cloud_provider", "aws"),
        ("aws_account_id", "043671579834"),
        ("aws_region", "us-west-1"),
        ("working_bucket_name", "bioaf-working-abc123"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()
    await cp.load_resolved_backends(session)
    try:
        from app.exceptions import ValidationError

        with pytest.raises(ValidationError, match="Image build is not deployed"):
            await build_cellxgene_image(session)
    finally:
        cp.reset_resolved_backends()
