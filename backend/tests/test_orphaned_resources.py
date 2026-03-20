"""Tests for orphaned resource tracking and cleanup."""

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
        "gcp_zone": "us-central1-a",
        "terraform_state_bucket": "bioaf-tfstate-test",
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


# ---------------------------------------------------------------------------
# OrphanedResourceService — log, list, has_orphaned_for_uid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_orphaned_resource(session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1-a",
    )
    await session.flush()

    assert resource.id is not None
    assert resource.status == "detected"
    assert resource.resource_type == "gke_cluster"
    assert resource.resource_name == "bioaf-demo-abc123"


@pytest.mark.asyncio
async def test_list_orphaned_resources(session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await OrphanedResourceService.log_resource(
        session,
        resource_type="gcs_bucket",
        resource_name="bioaf-ingest-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()

    all_resources = await OrphanedResourceService.list_resources(session)
    assert len(all_resources) == 2

    clusters = await OrphanedResourceService.list_resources(session, status="detected")
    assert len(clusters) == 2


@pytest.mark.asyncio
async def test_list_orphaned_resources_filter_by_status(session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    r = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()

    # Manually set status to cleaned
    await session.execute(text("UPDATE orphaned_resources SET status = 'cleaned' WHERE id = :id").bindparams(id=r.id))
    await session.flush()

    detected = await OrphanedResourceService.list_resources(session, status="detected")
    assert len(detected) == 0

    cleaned = await OrphanedResourceService.list_resources(session, status="cleaned")
    assert len(cleaned) == 1


@pytest.mark.asyncio
async def test_has_orphaned_for_uid(session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    assert await OrphanedResourceService.has_orphaned_for_uid(session, "abc123") is False

    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()

    assert await OrphanedResourceService.has_orphaned_for_uid(session, "abc123") is True
    assert await OrphanedResourceService.has_orphaned_for_uid(session, "def456") is False


@pytest.mark.asyncio
async def test_has_orphaned_for_uid_ignores_resolved(session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    r = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()

    # Resolve it
    await session.execute(text("UPDATE orphaned_resources SET status = 'cleaned' WHERE id = :id").bindparams(id=r.id))
    await session.flush()

    assert await OrphanedResourceService.has_orphaned_for_uid(session, "abc123") is False


# ---------------------------------------------------------------------------
# OrphanedResourceService — cleanup and dismiss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_gke_cluster(session, admin_user):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1-a",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.delete_cluster = MagicMock(return_value=MagicMock())

    with (
        patch(
            "app.services.stack_deployment._get_gke_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.stack_deployment._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.cleanup_resource(session, resource.id, admin_user.id)

    assert result.status == "cleaned"
    assert result.resolved_at is not None
    assert result.resolved_by_user_id == admin_user.id
    mock_client.delete_cluster.assert_called_once_with(
        name="projects/test-project/locations/us-central1-a/clusters/bioaf-demo-abc123"
    )


@pytest.mark.asyncio
async def test_cleanup_gcs_bucket(session, admin_user):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gcs_bucket",
        resource_name="bioaf-ingest-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()

    mock_bucket = MagicMock()
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket

    with (
        patch(
            "app.services.stack_deployment._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "app.services.orphaned_resource_service.storage.Client",
            return_value=mock_storage_client,
        ),
    ):
        result = await OrphanedResourceService.cleanup_resource(session, resource.id, admin_user.id)

    assert result.status == "cleaned"
    mock_storage_client.bucket.assert_called_once_with("bioaf-ingest-demo-abc123")
    mock_bucket.delete.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_cleanup_failure_sets_status_failed(session, admin_user):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1-a",
    )
    await session.flush()

    mock_client = MagicMock()
    mock_client.delete_cluster.side_effect = Exception("GKE API error: cluster not found")

    with (
        patch(
            "app.services.stack_deployment._get_gke_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.stack_deployment._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        result = await OrphanedResourceService.cleanup_resource(session, resource.id, admin_user.id)

    assert result.status == "failed"
    assert "GKE API error" in (result.error_message or "")


@pytest.mark.asyncio
async def test_dismiss_resource(session, admin_user):
    from app.services.orphaned_resource_service import OrphanedResourceService

    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()

    result = await OrphanedResourceService.dismiss_resource(session, resource.id, admin_user.id)

    assert result.status == "dismissed"
    assert result.resolved_at is not None
    assert result.resolved_by_user_id == admin_user.id


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_orphaned_resources_endpoint(client: AsyncClient, admin_token: str, session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()
    await session.commit()

    response = await client.get(
        "/api/v1/infrastructure/orphaned-resources",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["resource_name"] == "bioaf-demo-abc123"


@pytest.mark.asyncio
async def test_cleanup_orphaned_resource_endpoint(client: AsyncClient, admin_token: str, session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    await _seed_platform_config(session)
    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
        gcp_zone="us-central1-a",
    )
    await session.flush()
    await session.commit()

    mock_client = MagicMock()
    mock_client.delete_cluster = MagicMock(return_value=MagicMock())

    with (
        patch(
            "app.services.stack_deployment._get_gke_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.stack_deployment._get_gke_credentials",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        response = await client.post(
            f"/api/v1/infrastructure/orphaned-resources/{resource.id}/cleanup",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "cleaned"


@pytest.mark.asyncio
async def test_dismiss_orphaned_resource_endpoint(client: AsyncClient, admin_token: str, session):
    from app.services.orphaned_resource_service import OrphanedResourceService

    resource = await OrphanedResourceService.log_resource(
        session,
        resource_type="gke_cluster",
        resource_name="bioaf-demo-abc123",
        gcp_project_id="test-project",
        stack_uid="abc123",
    )
    await session.flush()
    await session.commit()

    response = await client.post(
        f"/api/v1/infrastructure/orphaned-resources/{resource.id}/dismiss",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"


@pytest.mark.asyncio
async def test_orphaned_resources_require_admin(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/v1/infrastructure/orphaned-resources",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
