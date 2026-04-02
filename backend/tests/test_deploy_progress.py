"""Tests for the deploy progress polling endpoint.

1. test_progress_no_active_deploy -- returns active=false when idle
2. test_progress_active_deploy_storage_phase -- returns phase/resources during storage
3. test_progress_active_deploy_compute_phase -- returns phase/resources during compute
4. test_progress_completed_deploy -- returns active=false after completion
5. test_progress_failed_deploy -- returns error_message on failure
6. test_progress_requires_permission -- viewer gets 403
"""

import pytest
from sqlalchemy import text


async def _set_config(session, key: str, value: str):
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(k=key, v=value)
    )
    await session.flush()


async def _create_run(
    session,
    *,
    user_id: int,
    action: str = "apply",
    module_name: str = "storage",
    status: str = "applying",
    resources_planned: int | None = None,
    resources_completed: int = 0,
    deploy_phase: str | None = None,
    completed_resources: list | None = None,
    error_message: str | None = None,
) -> int:
    from app.models.component import TerraformRun

    run = TerraformRun(
        triggered_by_user_id=user_id,
        action=action,
        module_name=module_name,
        status=status,
        resources_planned=resources_planned,
        resources_completed=resources_completed,
        deploy_phase=deploy_phase,
        completed_resources=completed_resources,
        error_message=error_message,
    )
    session.add(run)
    await session.flush()
    return run.id


class TestDeployProgressEndpoint:
    @pytest.mark.asyncio
    async def test_progress_no_active_deploy(self, client, admin_token, session):
        """Returns active=false when no deployment is running."""
        response = await client.get(
            "/api/v1/infrastructure/stack/deploy/progress",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False
        assert data["phase"] is None
        assert data["resources_completed"] == 0
        assert data["resources_total"] == 0

    @pytest.mark.asyncio
    async def test_progress_active_deploy_storage_phase(self, client, admin_token, admin_user, session):
        """Returns storage phase progress during active deploy."""
        run_id = await _create_run(
            session,
            user_id=admin_user.id,
            action="apply",
            module_name="storage",
            status="applying",
            resources_planned=8,
            resources_completed=3,
            deploy_phase="storage",
            completed_resources=[
                "google_storage_bucket.ingest",
                "google_storage_bucket.raw",
                "google_storage_bucket.working",
            ],
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack/deploy/progress",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["status"] == "applying"
        assert data["phase"] == "storage"
        assert data["resources_completed"] == 3
        assert data["resources_total"] == 8
        assert len(data["completed_resources"]) == 3
        assert "google_storage_bucket.ingest" in data["completed_resources"]
        assert data["run_id"] == run_id

    @pytest.mark.asyncio
    async def test_progress_active_deploy_compute_phase(self, client, admin_token, admin_user, session):
        """Returns compute phase progress during active deploy."""
        run_id = await _create_run(
            session,
            user_id=admin_user.id,
            action="apply",
            module_name="compute",
            status="applying",
            resources_planned=16,
            resources_completed=10,
            deploy_phase="compute",
            completed_resources=[
                "google_container_cluster.bioaf",
                "google_container_node_pool.pipelines",
            ],
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack/deploy/progress",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["phase"] == "compute"
        assert data["resources_completed"] == 10
        assert data["resources_total"] == 16
        assert data["run_id"] == run_id

    @pytest.mark.asyncio
    async def test_progress_completed_deploy(self, client, admin_token, admin_user, session):
        """Returns active=false after deployment completes."""
        await _create_run(
            session,
            user_id=admin_user.id,
            action="apply",
            module_name="compute",
            status="completed",
            resources_planned=16,
            resources_completed=16,
            deploy_phase="compute",
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack/deploy/progress",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False

    @pytest.mark.asyncio
    async def test_progress_failed_deploy(self, client, admin_token, admin_user, session):
        """Returns error_message on failed deployment."""
        await _create_run(
            session,
            user_id=admin_user.id,
            action="apply",
            module_name="storage",
            status="failed",
            resources_planned=8,
            resources_completed=3,
            deploy_phase="storage",
            error_message="Quota exceeded for storage buckets",
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack/deploy/progress",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # Failed runs are not active
        assert data["active"] is False

    @pytest.mark.asyncio
    async def test_progress_planning_is_active(self, client, admin_token, admin_user, session):
        """Planning phase counts as active."""
        await _create_run(
            session,
            user_id=admin_user.id,
            action="apply",
            module_name="storage",
            status="planning",
            deploy_phase="storage",
        )
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack/deploy/progress",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["status"] == "planning"

    @pytest.mark.asyncio
    async def test_progress_requires_permission(self, client, viewer_token, session):
        """Viewer role gets 403."""
        response = await client.get(
            "/api/v1/infrastructure/stack/deploy/progress",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403
