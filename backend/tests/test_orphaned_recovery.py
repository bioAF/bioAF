"""Tests for orphaned resource recovery: recovery-check, adopt, and cleanup-all."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_platform_config(session, overrides: dict | None = None):
    """Seed platform_config keys needed by orphaned resource service."""
    defaults = {
        "gcp_credential_source": "service_account_key",
        "gcp_service_account_key": '{"type":"service_account","project_id":"test"}',
        "gcp_project_id": "test-project",
        "gcp_region": "us-central1",
        "terraform_state_bucket": "bioaf-tfstate-test",
        "terraform_initialized": "true",
        "compute_deployed": "false",
        "compute_stack": "null",
        "storage_deployed": "true",
        "org_slug": "demo",
    }
    if overrides:
        defaults.update(overrides)
    for key, value in defaults.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


def _make_gke_cluster(status_code: int = 2, name: str = "bioaf-demo-abc123"):
    """Create a mock GKE cluster object.

    Status codes: 1=PROVISIONING, 2=RUNNING, 5=ERROR.
    """
    cluster = MagicMock()
    cluster.name = name
    cluster.status = status_code
    cluster.current_node_count = 3
    cluster.endpoint = "10.0.0.1"
    cluster.master_auth.cluster_ca_certificate = "fake-ca-cert"

    pool = MagicMock()
    pool.name = "bioaf-pipelines"
    pool.config.machine_type = "e2-standard-4"
    pool.config.spot = False
    pool.autoscaling.min_node_count = 0
    pool.autoscaling.max_node_count = 3
    pool.initial_node_count = 1
    pool.status = 2
    cluster.node_pools = [pool]
    return cluster


# ---------------------------------------------------------------------------
# Service: recovery_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_check_running_cluster(session, admin_user):
    """A RUNNING orphaned cluster should be flagged as recoverable."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=2)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.recovery_check(session)

    assert len(result["recoverable"]) == 1
    assert result["recoverable"][0]["gke_status"] == "RUNNING"
    assert len(result["provisioning"]) == 0
    assert len(result["dead"]) == 0


@pytest.mark.asyncio
async def test_recovery_check_provisioning_cluster(session, admin_user):
    """A PROVISIONING cluster should be reported as still waiting."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=1)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.recovery_check(session)

    assert len(result["recoverable"]) == 0
    assert len(result["provisioning"]) == 1
    assert result["provisioning"][0]["gke_status"] == "PROVISIONING"


@pytest.mark.asyncio
async def test_recovery_check_dead_cluster(session, admin_user):
    """A cluster that returns NOT_FOUND should be classified as dead."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.side_effect = Exception("404 Not Found")

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.recovery_check(session)

    assert len(result["recoverable"]) == 0
    assert len(result["provisioning"]) == 0
    assert len(result["dead"]) == 1


@pytest.mark.asyncio
async def test_recovery_check_no_orphans(session, admin_user):
    """With no orphaned resources, all lists should be empty."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    result = await OrphanedResourceService.recovery_check(session)

    assert result["recoverable"] == []
    assert result["provisioning"] == []
    assert result["dead"] == []


@pytest.mark.asyncio
async def test_recovery_check_ignores_non_cluster_orphans(session, admin_user):
    """GCS bucket orphans should be ignored by recovery check."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gcs_bucket",
        resource_name="bioaf-ingest-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()

    result = await OrphanedResourceService.recovery_check(session)

    assert result["recoverable"] == []
    assert result["provisioning"] == []
    assert result["dead"] == []


@pytest.mark.asyncio
async def test_recovery_check_error_cluster(session, admin_user):
    """A cluster in ERROR state should be classified as dead."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=5)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.recovery_check(session)

    assert len(result["dead"]) == 1
    assert result["dead"][0]["gke_status"] == "ERROR"


# ---------------------------------------------------------------------------
# Service: adopt_resource
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adopt_running_cluster(session, admin_user):
    """Adopting a RUNNING cluster should populate platform_config and mark resolved."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=2)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.adopt_resource(session, resource.id, admin_user.id)

    assert result.status == "adopted"
    assert result.resolved_at is not None
    assert result.resolved_by_user_id == admin_user.id

    # Verify platform_config was populated
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'compute_deployed'"))).fetchone()
    assert row[0] == "true"

    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'gke_cluster_name'"))).fetchone()
    assert row[0] == "bioaf-demo-abc123"

    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'compute_stack'"))).fetchone()
    assert row[0] == "kubernetes"


@pytest.mark.asyncio
async def test_adopt_provisioning_cluster_rejected(session, admin_user):
    """Cannot adopt a cluster that is still provisioning."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=1)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        with pytest.raises(ValueError, match="not in a running state"):
            await OrphanedResourceService.adopt_resource(session, resource.id, admin_user.id)


@pytest.mark.asyncio
async def test_adopt_nonexistent_resource_rejected(session, admin_user):
    """Adopting a non-existent resource should raise."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    with pytest.raises(ValueError, match="not found"):
        await OrphanedResourceService.adopt_resource(session, 9999, admin_user.id)


# ---------------------------------------------------------------------------
# Service: cleanup_dead_orphans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_dead_orphans(session, admin_user):
    """cleanup_dead_orphans should delete NOT_FOUND clusters and dismiss them."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.side_effect = Exception("404 Not Found")

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.cleanup_dead_orphans(session, admin_user.id)

    assert result["cleaned"] == 1
    assert result["skipped"] == 0

    # Verify the resource is now resolved
    resources = await OrphanedResourceService.list_resources(session, status="detected")
    assert len(resources) == 0


@pytest.mark.asyncio
async def test_cleanup_dead_orphans_skips_running(session, admin_user):
    """cleanup_dead_orphans should skip RUNNING clusters (they should be adopted)."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=2)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.cleanup_dead_orphans(session, admin_user.id)

    assert result["cleaned"] == 0
    assert result["skipped"] == 1


@pytest.mark.asyncio
async def test_cleanup_dead_deletes_error_clusters(session, admin_user):
    """cleanup_dead_orphans should delete ERROR clusters from GCP."""
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=5)
    mock_client.delete_cluster = MagicMock(return_value=MagicMock())

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.cleanup_dead_orphans(session, admin_user.id)

    assert result["cleaned"] == 1
    mock_client.delete_cluster.assert_called_once()


# ---------------------------------------------------------------------------
# StackStatus includes has_orphaned_clusters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stack_status_includes_orphaned_flag(session, admin_user):
    """StackStatus should report has_orphaned_clusters when unresolved orphans exist."""
    from app.services.orphaned_resource_service import OrphanedResourceService
    from app.services.stack_deployment import get_cluster_status

    await _seed_platform_config(session)

    status = await get_cluster_status(session)
    assert status.has_orphaned_clusters is False

    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()

    status = await get_cluster_status(session)
    assert status.has_orphaned_clusters is True


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_check_endpoint(client: AsyncClient, admin_token: str, session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()
    await session.commit()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=2)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        response = await client.get(
            "/api/v1/infrastructure/orphaned-resources/recovery-check",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["recoverable"]) == 1
    assert data["recoverable"][0]["gke_status"] == "RUNNING"


@pytest.mark.asyncio
async def test_adopt_endpoint(client: AsyncClient, admin_token: str, session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()
    await session.commit()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=2)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        response = await client.post(
            f"/api/v1/infrastructure/orphaned-resources/{resource.id}/adopt",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "adopted"


@pytest.mark.asyncio
async def test_adopt_endpoint_rejects_provisioning(client: AsyncClient, admin_token: str, session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()
    await session.commit()

    mock_client = MagicMock()
    mock_client.get_cluster.return_value = _make_gke_cluster(status_code=1)

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        response = await client.post(
            f"/api/v1/infrastructure/orphaned-resources/{resource.id}/adopt",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_cleanup_all_endpoint(client: AsyncClient, admin_token: str, session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1",
    )
    await session.flush()
    await session.commit()

    mock_client = MagicMock()
    mock_client.get_cluster.side_effect = Exception("404 Not Found")

    with (
        patch("app.services.orphaned_resource_service._get_gke_client", return_value=mock_client),
        patch(
            "app.services.orphaned_resource_service._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        response = await client.post(
            "/api/v1/infrastructure/orphaned-resources/cleanup-all",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["cleaned"] >= 1


@pytest.mark.asyncio
async def test_recovery_check_requires_permission(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/v1/infrastructure/orphaned-resources/recovery-check",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
