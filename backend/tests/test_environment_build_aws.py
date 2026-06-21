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
    # ECR is one repo per image: the per-environment repo is ensured under the
    # bioaf-env- namespace so it matches the CodeBuild role's bioaf-* push scope.
    ecr_mk.return_value.create_repository.assert_called_once_with(repositoryName="bioaf-env-custom-env")
    # CodeBuild targets the project with the ECR image URI, tagged v<version>.<build>.
    kwargs = cb_mk.return_value.start_build.call_args.kwargs
    assert kwargs["projectName"] == "bioaf-image-build"
    env_vars = {e["name"]: e["value"] for e in kwargs["environmentVariablesOverride"]}
    assert env_vars["IMAGE_URI"] == "043671579834.dkr.ecr.us-west-1.amazonaws.com/bioaf-env-custom-env:v1.1"
    # The version record reflects the build.
    await session.refresh(version)
    assert version.status == "building"
    assert version.build_id == "bioaf-image-build:run-7"
    assert version.image_uri == "043671579834.dkr.ecr.us-west-1.amazonaws.com/bioaf-env-custom-env:v1.1"


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


# --- 8b: work-node AMI build (Packer via CodeBuild) ---------------------------


async def _make_draft_conda_worknode_env(session, admin_user, name: str) -> tuple[Environment, EnvironmentVersion]:
    """Create a work_node environment + a draft conda version."""
    env = Environment(
        name=name,
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="work_node",
        visibility="team",
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        build_number=1,
        status="draft",
        definition_format="conda",
        definition_content="name: bioaf-work\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    return env, version


@pytest.mark.asyncio
async def test_build_version_work_node_on_aws_submits_codebuild_packer(session, admin_user, aws_install):
    """A work-node env build on AWS StartBuilds a Packer AMI build; image_uri is the AMI name."""
    from app.adapters.image_build.aws import CodeBuildImageBuildProvider

    env, version = await _make_draft_conda_worknode_env(session, admin_user, "Bench Env")

    storage = MagicMock()
    storage.build_uri.side_effect = lambda b, k: f"s3://{b}/{k}"
    storage.upload_file = AsyncMock()

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=storage),
        patch.object(CodeBuildImageBuildProvider, "_client") as cb_mk,
    ):
        cb_mk.return_value.start_build.return_value = {"build": {"id": "bioaf-image-build:ami-run-1"}}
        build_id = await EnvironmentBuildService.build_version(
            session, admin_user.organization_id, admin_user.id, env.id, version.id
        )

    assert build_id == "bioaf-image-build:ami-run-1"
    kwargs = cb_mk.return_value.start_build.call_args.kwargs
    assert kwargs["projectName"] == "bioaf-image-build"
    # The Packer buildspec (not the docker one) drives the build.
    assert "packer build" in kwargs["buildspecOverride"]
    # Packer inputs are passed as PKR_VAR_* env overrides.
    env_vars = {e["name"]: e["value"] for e in kwargs["environmentVariablesOverride"]}
    assert env_vars["PKR_VAR_image_name"] == "bioaf-worknode-bench-env-v1-1"
    assert env_vars["PKR_VAR_region"] == "us-west-1"
    assert env_vars["PKR_VAR_conda_env_name"] == "bioaf-work"
    # The work-node image_uri is the deterministic AMI name (launch resolves name->id).
    await session.refresh(version)
    assert version.status == "building"
    assert version.build_id == "bioaf-image-build:ami-run-1"
    assert version.image_uri == "bioaf-worknode-bench-env-v1-1"


@pytest.mark.asyncio
async def test_build_version_work_node_on_aws_requires_conda(session, admin_user, aws_install):
    """Work-node envs only support conda definitions (same rule as GCP)."""
    env = Environment(
        name="Dockerfile Worknode",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="work_node",
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
        definition_content="FROM ubuntu:22.04",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()

    from app.exceptions import ValidationError

    with pytest.raises(ValidationError, match="only support conda"):
        await EnvironmentBuildService.build_version(
            session, admin_user.organization_id, admin_user.id, env.id, version.id
        )


def test_cloud_build_provider_cannot_build_vm_images():
    """The Cloud Build (GCP) provider has no VM-image build; it keeps the inline path."""
    from app.adapters.image_build.gcp import GcpCloudBuildProvider

    with pytest.raises(NotImplementedError):
        GcpCloudBuildProvider().submit_vm_image_build(
            None, {"project_id": "p"}, context_object_uri="gs://b/o", image_name="img", build_vars={}, timeout="1s"
        )


def test_aws_packer_template_is_amazon_ebs_with_force_deregister():
    """The AWS work-node Packer template builds an amazon-ebs AMI and is rebuild-safe."""
    from app.services.environment_build_service import PACKER_VM_TEMPLATE_AWS

    assert 'source "amazon-ebs"' in PACKER_VM_TEMPLATE_AWS
    # Rebuilds reuse the deterministic AMI name, so a prior AMI must be deregistered.
    assert "force_deregister      = true" in PACKER_VM_TEMPLATE_AWS
    # environment.yml is shipped to the builder (no S3/IAM needed on the build box).
    assert 'provisioner "file"' in PACKER_VM_TEMPLATE_AWS
    assert "amazon/aws-cli" not in PACKER_VM_TEMPLATE_AWS  # builder uses OS awscli, not a container


@pytest.mark.asyncio
async def test_dockerfile_build_context_substitutes_storage_placeholder():
    """A custom Dockerfile carrying the system template's __STORAGE_PIP_PACKAGES__
    placeholder (a common copy-from-template mistake) is filled in at build-context
    upload, so `docker build` does not fail with 'Invalid requirement'."""
    import io as _io
    import tarfile as _tarfile

    from app.services.environment_build_service import _upload_version_build_context

    version = MagicMock()
    version.definition_format = "dockerfile"
    version.definition_content = "FROM python:3.11\nRUN pip install scanpy __STORAGE_PIP_PACKAGES__\n"
    version.version_number = 1

    captured: dict = {}
    storage = MagicMock()
    storage.build_uri.side_effect = lambda b, k: f"s3://{b}/{k}"
    storage.image_storage_pip_packages.return_value = "'boto3>=1.43,<2' 'awscli>=1.40,<2'"

    async def _capture(uri, buf, content_type=None):
        captured["data"] = buf.getvalue()

    storage.upload_file = AsyncMock(side_effect=_capture)

    with patch("app.adapters.registry.get_storage_adapter", return_value=storage):
        await _upload_version_build_context(None, "bioaf-working", version, "My Env")

    tf = _tarfile.open(fileobj=_io.BytesIO(captured["data"]), mode="r:gz")
    dockerfile = tf.extractfile("Dockerfile").read().decode()
    assert "__STORAGE_PIP_PACKAGES__" not in dockerfile
    assert "boto3" in dockerfile
