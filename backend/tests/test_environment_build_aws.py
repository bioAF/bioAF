"""AWS-path + GCP byte-identity tests for the environment (notebook/pipeline)
container-image build (cleanup item 8a).

``EnvironmentBuildService._build_docker_image`` used to call Cloud Build REST +
Artifact Registry directly (reading ``gcp_project_id``), so building a custom
notebook environment raised "GCP project not configured" on an AWS install. It is
now routed through the ImageRegistry + ImageBuild seams via
``resolve_image_platform`` (Cloud Build -> Artifact Registry on GCP; CodeBuild ->
ECR on AWS), exactly like ``notebook_image_service``.

These prove (1) an AWS install ensures the per-env ECR repo and submits a CodeBuild
build with the ECR image URI, (2) the GCP path is byte-identical (same AR URI +
Cloud Build body), and (3) the build poller's gate is cloud-neutral (``has_target``,
not ``gcp_project_id``). boto3 / the GCP REST primitive are mocked at the provider
seam; storage I/O is stubbed. DB-bound (platform_config + the resolved-backend
cache), so CI-only locally per the repo conventions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

import app.platform.cloud_provider as cp
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.services.environment_build_service import EnvironmentBuildService


async def _seed_config(session, **kv: str) -> None:
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": k, "v": v},
        )
    await session.commit()


async def _make_draft_dockerfile_env(session, admin_user, name: str) -> tuple[Environment, EnvironmentVersion]:
    """Create a notebook (non-work_node) environment + a draft Dockerfile version."""
    env = Environment(
        name=name,
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="notebook",
        visibility="team",
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        build_number=1,
        status="draft",
        definition_format="dockerfile",
        definition_content="FROM python:3.11-slim",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    return env, version


@pytest_asyncio.fixture
async def aws_install(session):
    """Seed an AWS install + load the resolved-backend cache (ecr / codebuild)."""
    await _seed_config(
        session,
        cloud_provider="aws",
        aws_account_id="043671579834",
        aws_region="us-west-1",
        aws_codebuild_project="bioaf-image-build",
        working_bucket_name="bioaf-working-abc123",
    )
    await cp.load_resolved_backends(session)
    try:
        yield
    finally:
        cp.reset_resolved_backends()


@pytest.mark.asyncio
async def test_build_version_docker_on_aws_routes_through_ecr_codebuild(session, admin_user, aws_install):
    """A custom notebook env build on AWS ensures its ECR repo and StartBuilds CodeBuild."""
    from app.adapters.image_build.aws import CodeBuildImageBuildProvider
    from app.adapters.image_registry.aws import EcrImageRegistryProvider

    env, version = await _make_draft_dockerfile_env(session, admin_user, "Custom Env")

    storage = MagicMock()
    storage.build_uri.side_effect = lambda b, k: f"s3://{b}/{k}"
    storage.upload_file = AsyncMock()

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=storage),
        patch.object(EcrImageRegistryProvider, "_client") as ecr_mk,
        patch.object(CodeBuildImageBuildProvider, "_client") as cb_mk,
    ):
        cb_mk.return_value.start_build.return_value = {"build": {"id": "bioaf-image-build:run-7"}}
        build_id = await EnvironmentBuildService.build_version(
            session, admin_user.organization_id, admin_user.id, env.id, version.id
        )

    assert build_id == "bioaf-image-build:run-7"
    # ECR is one repo per image: the per-environment repo (safe name) is ensured.
    ecr_mk.return_value.create_repository.assert_called_once_with(repositoryName="custom-env")
    # CodeBuild targets the project with the ECR image URI, tagged v<version>.<build>.
    kwargs = cb_mk.return_value.start_build.call_args.kwargs
    assert kwargs["projectName"] == "bioaf-image-build"
    env_vars = {e["name"]: e["value"] for e in kwargs["environmentVariablesOverride"]}
    assert env_vars["IMAGE_URI"] == "043671579834.dkr.ecr.us-west-1.amazonaws.com/custom-env:v1.1"
    # The version record reflects the build.
    await session.refresh(version)
    assert version.status == "building"
    assert version.build_id == "bioaf-image-build:run-7"
    assert version.image_uri == "043671579834.dkr.ecr.us-west-1.amazonaws.com/custom-env:v1.1"


@pytest.mark.asyncio
async def test_build_version_docker_on_aws_raises_without_codebuild_project(session, admin_user):
    """AWS install with account+region but no deployed CodeBuild project fails clearly."""
    await _seed_config(
        session,
        cloud_provider="aws",
        aws_account_id="043671579834",
        aws_region="us-west-1",
        working_bucket_name="bioaf-working-abc123",
    )
    await cp.load_resolved_backends(session)
    try:
        env, version = await _make_draft_dockerfile_env(session, admin_user, "No Build Env")
        from app.exceptions import ValidationError

        with pytest.raises(ValidationError, match="Image build is not deployed"):
            await EnvironmentBuildService.build_version(
                session, admin_user.organization_id, admin_user.id, env.id, version.id
            )
    finally:
        cp.reset_resolved_backends()


@pytest.mark.asyncio
async def test_build_version_docker_on_gcp_is_byte_identical(session, admin_user):
    """The GCP path is unchanged: AR image URI + a Cloud Build storageSource build."""
    await _seed_config(
        session,
        gcp_project_id="bioaf-test",
        gcp_region="us-central1",
        gcp_service_account_email="sa@bioaf-test.iam.gserviceaccount.com",
        working_bucket_name="bioaf-working-xyz",
    )

    env, version = await _make_draft_dockerfile_env(session, admin_user, "GCP Env")

    storage = MagicMock()
    storage.build_uri.side_effect = lambda b, k: f"gs://{b}/{k}"
    storage.upload_file = AsyncMock()

    captured: dict = {}

    def fake_authorized_request(credentials, method, url, body=None):
        if method == "POST" and url.endswith("/builds"):
            captured["body"] = body
            return {"metadata": {"build": {"id": "gcp-build-1"}}}
        # ensure_repository GET (repo exists) / any other call.
        return {}

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=storage),
        patch("app.services.notebook_image_service._get_credentials", AsyncMock(return_value=MagicMock())),
        patch("app.adapters.image_build.gcp.authorized_request", side_effect=fake_authorized_request),
        patch("app.adapters.image_registry.gcp.authorized_request", side_effect=fake_authorized_request),
    ):
        build_id = await EnvironmentBuildService.build_version(
            session, admin_user.organization_id, admin_user.id, env.id, version.id
        )

    assert build_id == "gcp-build-1"
    expected_uri = "us-central1-docker.pkg.dev/bioaf-test/bioaf-images/gcp-env:v1.1"
    await session.refresh(version)
    assert version.image_uri == expected_uri
    assert version.status == "building"

    body = captured["body"]
    assert body["source"]["storageSource"]["bucket"] == "bioaf-working-xyz"
    assert body["images"] == [expected_uri]
    assert body["steps"][0]["args"] == ["build", "-t", expected_uri, "-f", "Dockerfile", "."]
    assert body["serviceAccount"] == "projects/bioaf-test/serviceAccounts/sa@bioaf-test.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_poll_in_progress_builds_on_aws_uses_has_target_gate(session, admin_user):
    """The poller gates on the cloud target (account+region), not gcp_project_id, so
    AWS builds transition out of 'building'."""
    await _seed_config(
        session,
        cloud_provider="aws",
        aws_account_id="043671579834",
        aws_region="us-west-1",
    )
    await cp.load_resolved_backends(session)
    try:
        env, version = await _make_draft_dockerfile_env(session, admin_user, "AWS Poll Env")
        version.status = "building"
        version.build_id = "bioaf-image-build:run-9"
        await session.flush()

        with patch(
            "app.services.notebook_image_service.check_build_status",
            new=AsyncMock(return_value="SUCCESS"),
        ) as mock_cbs:
            changed = await EnvironmentBuildService.poll_in_progress_builds(session)

        mock_cbs.assert_awaited_once_with(session, "bioaf-image-build:run-9")
        assert changed == 1
        await session.refresh(version)
        assert version.status == "ready"
    finally:
        cp.reset_resolved_backends()


@pytest.mark.asyncio
async def test_poll_in_progress_builds_noop_without_cloud_target(session, admin_user):
    """With no cloud target configured the poller no-ops (does not touch builds)."""
    env, version = await _make_draft_dockerfile_env(session, admin_user, "Unconfigured Env")
    version.status = "building"
    version.build_id = "build-abc"
    await session.flush()

    with patch("app.services.notebook_image_service.check_build_status", new=AsyncMock()) as mock_cbs:
        changed = await EnvironmentBuildService.poll_in_progress_builds(session)

    mock_cbs.assert_not_awaited()
    assert changed == 0
    await session.refresh(version)
    assert version.status == "building"
