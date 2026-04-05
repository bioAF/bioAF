"""Tests for dynamic GCS bucket name in notebook and work node sessions."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_session_uses_configured_working_bucket(session, admin_user):
    """NotebookService passes working_bucket from platform_config to the adapter."""
    from app.services.notebook_service import NotebookService

    # Seed platform_config with a custom working bucket name
    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = :v"),
        {"k": "working_bucket_name", "v": "bioaf-working-myorg-abc123"},
    )
    await session.flush()

    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
    )

    # The local adapter stores gcs_home_prefix in the session record
    assert ns.gcs_home_prefix is not None
    assert "bioaf-working-myorg-abc123" in ns.gcs_home_prefix
    assert "bioaf-working/" not in ns.gcs_home_prefix


@pytest.mark.asyncio
async def test_session_falls_back_without_working_bucket(session, admin_user):
    """Without working_bucket_name in config, adapter uses default bucket name."""
    from app.services.notebook_service import NotebookService

    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="jupyter",
        resource_profile="small",
    )

    assert ns.gcs_home_prefix is not None
    assert "bioaf-working" in ns.gcs_home_prefix


@pytest.mark.asyncio
async def test_ssh_session_uses_configured_working_bucket(session, admin_user):
    """SSH work node sessions also use the configured working bucket."""
    from app.models.session_credential import SessionCredential
    from app.services.notebook_service import NotebookService

    # Seed working bucket
    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = :v"),
        {"k": "working_bucket_name", "v": "bioaf-working-custom-xyz"},
    )

    # SSH sessions require session credentials
    cred = SessionCredential(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        username="testadmin",
        password_hash=AuthService.hash_password("testpass123"),
    )
    session.add(cred)
    await session.flush()

    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="ssh",
        resource_profile="small",
    )

    assert ns.gcs_home_prefix is not None
    assert "bioaf-working-custom-xyz" in ns.gcs_home_prefix


@pytest.mark.asyncio
async def test_rstudio_session_uses_configured_working_bucket(session, admin_user):
    """RStudio sessions also use the configured working bucket."""
    from app.models.session_credential import SessionCredential
    from app.services.notebook_service import NotebookService

    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = :v"),
        {"k": "working_bucket_name", "v": "bioaf-working-rstudio-test"},
    )

    cred = SessionCredential(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        username="rstudiouser",
        password_hash=AuthService.hash_password("testpass123"),
    )
    session.add(cred)
    await session.flush()

    ns = await NotebookService.launch_session(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        session_type="rstudio",
        resource_profile="small",
    )

    assert ns.gcs_home_prefix is not None
    assert "bioaf-working-rstudio-test" in ns.gcs_home_prefix


# -- Work node (SSH) GCS bucket tests --


@pytest_asyncio.fixture
async def seed_env_version(session, admin_user):
    """Create a ready environment version for work node tests."""
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    env = Environment(
        name="GCS Test Env",
        description="Environment for GCS bucket tests",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="dockerfile",
        definition_content="FROM ubuntu:22.04",
        image_uri="us-central1-docker.pkg.dev/test/repo/test-env:v1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return version


@pytest_asyncio.fixture
async def seed_project(session, admin_user):
    """Create a project for work node tests."""
    from app.models.project import Project

    project = Project(
        name="GCS Test Project",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
    )
    session.add(project)
    await session.flush()
    await session.commit()
    return project


@pytest_asyncio.fixture
async def seed_admin_credentials(session, admin_user):
    """Set up session credentials for the admin user."""
    from app.services.session_credential_service import SessionCredentialService

    await SessionCredentialService.create_or_update(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        email=admin_user.email,
        password="testpass",
    )
    await session.commit()


@pytest.mark.asyncio
async def test_work_node_uses_configured_working_bucket(
    session, admin_user, seed_env_version, seed_project, seed_admin_credentials
):
    """WorkNodeService passes working_bucket from platform_config to the adapter."""
    from app.services.work_node_service import WorkNodeService

    await session.execute(
        text("INSERT INTO platform_config (key, value) VALUES (:k, :v) ON CONFLICT (key) DO UPDATE SET value = :v"),
        {"k": "working_bucket_name", "v": "bioaf-working-myorg-prod"},
    )
    await session.flush()

    cs = await WorkNodeService.launch_work_node(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        project_id=seed_project.id,
        environment_version_id=seed_env_version.id,
        machine_type="n2-standard-4",
    )

    assert cs.gcs_home_prefix is not None
    assert "bioaf-working-myorg-prod" in cs.gcs_home_prefix
    assert "bioaf-working/" not in cs.gcs_home_prefix


@pytest.mark.asyncio
async def test_work_node_falls_back_without_working_bucket(
    session, admin_user, seed_env_version, seed_project, seed_admin_credentials
):
    """Without working_bucket_name, work node adapter uses default bucket."""
    from app.services.work_node_service import WorkNodeService

    cs = await WorkNodeService.launch_work_node(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        project_id=seed_project.id,
        environment_version_id=seed_env_version.id,
        machine_type="n2-standard-4",
    )

    assert cs.gcs_home_prefix is not None
    assert "bioaf-working" in cs.gcs_home_prefix


@pytest.mark.asyncio
async def test_k8s_pod_manifest_has_gcsfuse_annotation():
    """Pod manifest includes gke-gcsfuse/volumes annotation when FUSE volumes are used."""
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    adapter = KubernetesNotebookProvider()
    # Build the pod manifest directly via _build_pod_manifest
    manifest = adapter._build_pod_manifest(
        session_spec={
            "session_type": "ssh",
            "session_id": 999,
            "user_id": 1,
            "image": "test-image:latest",
            "cpu_cores": 4,
            "memory_gb": 16,
            "node_pool": "interactive",
            "data_mount_paths": ["/pipeline-outputs/1"],
            "session_credentials": {"username": "testuser", "password_hash": "testhash"},
            "heartbeat_token": "test-token",
        }
    )

    annotations = manifest["metadata"].get("annotations", {})
    assert annotations.get("gke-gcsfuse/volumes") == "true"


@pytest.mark.asyncio
async def test_k8s_pod_manifest_no_gcsfuse_annotation_without_fuse():
    """Pod manifest omits FUSE annotation when no FUSE volumes are used."""
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    adapter = KubernetesNotebookProvider()
    manifest = adapter._build_pod_manifest(
        session_spec={
            "session_type": "jupyter",
            "session_id": 998,
            "user_id": 1,
            "image": "test-image:latest",
            "cpu_cores": 2,
            "memory_gb": 4,
            "node_pool": "interactive",
        }
    )

    annotations = manifest["metadata"].get("annotations", {})
    assert "gke-gcsfuse/volumes" not in annotations


@pytest.mark.asyncio
async def test_k8s_gcsfuse_volume_uses_configured_bucket():
    """GCS FUSE CSI volume uses the working_bucket from session spec, not hardcoded name."""
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    adapter = KubernetesNotebookProvider()
    manifest = adapter._build_pod_manifest(
        session_spec={
            "session_type": "ssh",
            "session_id": 997,
            "user_id": 1,
            "image": "test-image:latest",
            "cpu_cores": 4,
            "memory_gb": 16,
            "node_pool": "interactive",
            "working_bucket": "bioaf-working-custom-org",
            "data_mount_paths": ["/pipeline-outputs/1"],
            "session_credentials": {"username": "testuser", "password_hash": "testhash"},
            "heartbeat_token": "test-token",
        }
    )

    # Find the CSI volume
    volumes = manifest["spec"]["volumes"]
    csi_volumes = [v for v in volumes if "csi" in v]
    assert len(csi_volumes) > 0

    for vol in csi_volumes:
        bucket = vol["csi"]["volumeAttributes"]["bucketName"]
        assert bucket == "bioaf-working-custom-org", f"Expected configured bucket, got {bucket}"


@pytest.mark.asyncio
async def test_k8s_pod_manifest_mounts_gcs_secret():
    """Pod manifest mounts GCS SA key secret when has_gcs_secret=True."""
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    adapter = KubernetesNotebookProvider()
    manifest = adapter._build_pod_manifest(
        session_spec={
            "session_type": "jupyter",
            "session_id": 996,
            "user_id": 1,
            "image": "test-image:latest",
            "cpu_cores": 2,
            "memory_gb": 4,
            "node_pool": "interactive",
        },
        has_gcs_secret=True,
    )

    # Secret volume should be present
    volumes = manifest["spec"]["volumes"]
    secret_vols = [v for v in volumes if v.get("secret", {}).get("secretName") == "bioaf-gcs-sa-key"]
    assert len(secret_vols) == 1

    # Init containers should have the mount, env var, and auth activation in command
    for ic in manifest["spec"]["initContainers"]:
        mount_names = [m["name"] for m in ic.get("volumeMounts", [])]
        assert "gcp-sa-key" in mount_names, f"Init container {ic['name']} missing GCS secret mount"
        env_names = [e["name"] for e in ic.get("env", [])]
        assert "GOOGLE_APPLICATION_CREDENTIALS" in env_names
        # Command should have gcloud auth activation prepended
        cmd = ic.get("command", [])
        if len(cmd) >= 3 and cmd[0] == "/bin/sh":
            assert "gcloud auth activate-service-account" in cmd[2]

    # Main container should also have the env var and mount
    main = manifest["spec"]["containers"][0]
    mount_names = [m["name"] for m in main.get("volumeMounts", [])]
    assert "gcp-sa-key" in mount_names
    env_names = [e["name"] for e in main.get("env", [])]
    assert "GOOGLE_APPLICATION_CREDENTIALS" in env_names


@pytest.mark.asyncio
async def test_k8s_pod_manifest_no_gcs_secret_without_flag():
    """Pod manifest does not mount GCS secret when has_gcs_secret=False."""
    from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

    adapter = KubernetesNotebookProvider()
    manifest = adapter._build_pod_manifest(
        session_spec={
            "session_type": "jupyter",
            "session_id": 995,
            "user_id": 1,
            "image": "test-image:latest",
            "cpu_cores": 2,
            "memory_gb": 4,
            "node_pool": "interactive",
        },
        has_gcs_secret=False,
    )

    volumes = manifest["spec"]["volumes"]
    secret_vols = [v for v in volumes if "secret" in v]
    assert len(secret_vols) == 0
