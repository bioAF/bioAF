"""Infrastructure mutations that delete or create cloud resources need ``infrastructure:deploy``.

Per-orphan cleanup deletes clusters, buckets (with their contents) and service accounts; dismiss
marks one resolved. Storage deploy and update run a Terraform plan and apply. All four once needed
only ``infrastructure:view``, which the built-in comp_bio role holds. A caller without ``deploy``
is refused before the service runs: no cloud client, no Terraform, no status change. Listing
stays at ``view``. API keys carry no infrastructure scopes, so every key is refused.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.role import Role, RolePermission
from app.models.user import User
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


async def _custom_role_headers(session, admin_user, name: str, actions: list[str]) -> dict:
    role = Role(name=name, description=name, organization_id=admin_user.organization_id, is_system=False)
    session.add(role)
    await session.flush()
    session.add_all([RolePermission(role_id=role.id, resource="infrastructure", action=a) for a in actions])
    user = User(
        email=f"{name}@test.com",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=role.id,
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    token = AuthService.create_token(user.id, user.email, role.id, user.organization_id, role_name=name)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def view_only_headers(session, admin_user):
    return await _custom_role_headers(session, admin_user, "infra_viewer", ["view"])


@pytest_asyncio.fixture
async def deploy_headers(session, admin_user):
    return await _custom_role_headers(session, admin_user, "infra_deployer", ["view", "deploy"])


@pytest_asyncio.fixture
async def configure_headers(session, admin_user):
    return await _custom_role_headers(session, admin_user, "infra_configurer", ["view", "configure"])


@pytest_asyncio.fixture
async def orphan(session):
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
    await session.commit()
    return resource


async def _status(session, resource_id: int) -> str:
    from app.models.orphaned_resource import OrphanedResource

    await session.rollback()
    row = await session.get(OrphanedResource, resource_id, populate_existing=True)
    return row.status


def _cloud():
    gke = MagicMock()
    gke.delete_cluster = MagicMock(return_value=MagicMock())
    return gke


DENIED = ["comp_bio", "custom_view_only", "custom_configure", "api_key"]


@pytest_asyncio.fixture
async def denied_headers(request, comp_bio_token, view_only_headers, configure_headers, integration_api_key):
    return {
        "comp_bio": {"Authorization": f"Bearer {comp_bio_token}"},
        "custom_view_only": view_only_headers,
        "custom_configure": configure_headers,
        "api_key": integration_api_key["headers"],
    }


@pytest.mark.parametrize("caller", DENIED)
@pytest.mark.parametrize("action", ["cleanup", "dismiss"])
async def test_orphan_action_without_deploy_is_refused_before_anything_changes(
    client: AsyncClient, session, orphan, denied_headers, caller, action
):
    gke = _cloud()
    with (
        patch("app.adapters.compute.kubernetes.KubernetesComputeProvider._get_gke_client", return_value=gke),
        patch("app.services.stack_deployment._get_gke_credentials", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "app.services.orphaned_resource_service.OrphanedResourceService.cleanup_resource", new_callable=AsyncMock
        ) as cleanup,
        patch(
            "app.services.orphaned_resource_service.OrphanedResourceService.dismiss_resource", new_callable=AsyncMock
        ) as dismiss,
    ):
        response = await client.post(
            f"/api/v1/infrastructure/orphaned-resources/{orphan.id}/{action}", headers=denied_headers[caller]
        )

    assert response.status_code == 403
    cleanup.assert_not_called()
    dismiss.assert_not_called()
    gke.delete_cluster.assert_not_called()
    assert await _status(session, orphan.id) == "detected"


async def test_deploy_holder_can_dismiss_an_orphan(client: AsyncClient, session, orphan, deploy_headers):
    response = await client.post(f"/api/v1/infrastructure/orphaned-resources/{orphan.id}/dismiss", headers=deploy_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"


async def test_deploy_holder_can_clean_up_an_orphan(client: AsyncClient, session, orphan, deploy_headers):
    from tests.test_orphaned_resources import _seed_platform_config

    await _seed_platform_config(session)
    await session.commit()
    with (
        patch("app.adapters.compute.kubernetes.KubernetesComputeProvider._get_gke_client", return_value=_cloud()),
        patch("app.services.stack_deployment._get_gke_credentials", new_callable=AsyncMock, return_value=MagicMock()),
    ):
        response = await client.post(
            f"/api/v1/infrastructure/orphaned-resources/{orphan.id}/cleanup", headers=deploy_headers
        )
    assert response.status_code == 200
    assert response.json()["status"] == "cleaned"


async def test_view_still_lists_orphans(client: AsyncClient, orphan, view_only_headers):
    response = await client.get("/api/v1/infrastructure/orphaned-resources", headers=view_only_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.parametrize("caller", DENIED)
@pytest.mark.parametrize("path", ["deploy", "update"])
async def test_storage_terraform_without_deploy_is_refused(client: AsyncClient, denied_headers, caller, path):
    with patch("app.api.storage_deploy.deploy_storage_module", new_callable=AsyncMock) as module:
        response = await client.post(f"/api/v1/infrastructure/storage/{path}", headers=denied_headers[caller])
    assert response.status_code == 403
    module.assert_not_called()


@pytest.mark.parametrize("path", ["deploy", "update"])
async def test_deploy_holder_reaches_the_storage_preconditions(client: AsyncClient, deploy_headers, path):
    with patch("app.api.storage_deploy.deploy_storage_module", new_callable=AsyncMock) as module:
        response = await client.post(f"/api/v1/infrastructure/storage/{path}", headers=deploy_headers)
    # Terraform is not initialized in the test install, so the route stops at its own precondition.
    assert response.status_code == 400
    assert "Terraform has not been initialized" in response.json()["detail"]
    module.assert_not_called()
