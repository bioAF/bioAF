"""Tests for Phase 19 stack API endpoints (tests 14-23).

14. test_stack_deploy_requires_admin
15. test_stack_deploy_returns_sse
16. test_stack_status_returns_current_state
17. test_stack_teardown_requires_confirm
18. test_components_list_no_stack
19. test_components_list_kubernetes
20. test_component_toggle_enforces_dependencies
21. test_component_toggle_updates_state
22. test_cluster_config_returns_current_values
23. test_cluster_config_update_generates_plan
"""

from unittest.mock import AsyncMock, MagicMock, patch

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


async def _ensure_component(session, key: str, enabled: bool = False, status: str = "disabled"):
    await session.execute(
        text("""
        INSERT INTO component_states (component_key, enabled, status, config_json)
        VALUES (:key, :enabled, :status, '{}')
        ON CONFLICT (component_key) DO UPDATE SET enabled = :enabled, status = :status
        """).bindparams(key=key, enabled=enabled, status=status)
    )
    await session.flush()


# -----------------------------------------------------------------------
# Stack deploy / teardown / status
# -----------------------------------------------------------------------


class TestStackDeployEndpoint:
    @pytest.mark.asyncio
    async def test_stack_deploy_requires_admin(self, client, viewer_token, session):
        """Test 14: Stack deploy requires admin role."""
        response = await client.post(
            "/api/v1/infrastructure/stack/deploy",
            json={"stack_type": "kubernetes"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_stack_deploy_returns_sse(self, client, admin_token, session):
        """Test 15: Stack deploy returns SSE stream."""
        await _set_config(session, "gcp_credentials_configured", "true")
        await _set_config(session, "terraform_initialized", "true")
        await _set_config(session, "compute_deployed", "false")
        await _set_config(session, "storage_deployed", "true")
        await session.commit()

        from app.services.terraform_executor import TerraformProgressEvent

        async def mock_deploy(sess, stack_type, user_id, org_id=None):
            yield TerraformProgressEvent(event_type="stack_complete", message="done")

        with patch("app.api.stack_deploy.deploy_stack", side_effect=mock_deploy):
            response = await client.post(
                "/api/v1/infrastructure/stack/deploy",
                json={"stack_type": "kubernetes"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")


class TestStackStatusEndpoint:
    @pytest.mark.asyncio
    async def test_stack_status_returns_current_state(self, client, admin_token, session):
        """Test 16: Stack status returns correct StackStatus."""
        await _set_config(session, "compute_stack", "kubernetes")
        await _set_config(session, "compute_deployed", "false")
        await _set_config(session, "storage_deployed", "true")
        await session.commit()

        with patch("app.api.stack_deploy.get_cluster_status") as mock_status:
            from app.services.stack_deployment import StackStatus

            mock_status.return_value = StackStatus(
                compute_stack="kubernetes",
                compute_deployed=False,
                storage_deployed=True,
                cluster=None,
            )
            response = await client.get(
                "/api/v1/infrastructure/stack/status",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["compute_stack"] == "kubernetes"
        assert data["compute_deployed"] is False
        assert data["storage_deployed"] is True
        assert data["cluster"] is None


class TestStackTeardownEndpoint:
    @pytest.mark.asyncio
    async def test_stack_teardown_requires_confirm(self, client, admin_token, session):
        """Test 17: Teardown requires confirm=true."""
        response = await client.post(
            "/api/v1/infrastructure/stack/teardown",
            json={"confirm": False},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400


# -----------------------------------------------------------------------
# Components list and toggle
# -----------------------------------------------------------------------


class TestComponentsListEndpoint:
    @pytest.mark.asyncio
    async def test_components_list_no_stack(self, client, admin_token, session):
        """Test 18: No stack returns empty components list."""
        await _set_config(session, "compute_stack", "null")
        await _set_config(session, "compute_deployed", "false")
        await _set_config(session, "storage_deployed", "false")
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["compute_stack"] is None
        assert len(data["components"]) == 0

    @pytest.mark.asyncio
    async def test_components_list_kubernetes(self, client, admin_token, session):
        """Test 19: Kubernetes stack returns K8s components with full names."""
        await _set_config(session, "compute_stack", "kubernetes")
        await _set_config(session, "compute_deployed", "true")
        await _set_config(session, "storage_deployed", "true")
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["compute_stack"] == "kubernetes"

        component_keys = [c["key"] for c in data["components"]]
        assert "nextflow" in component_keys
        assert "snakemake" in component_keys
        assert "jupyterhub" in component_keys
        assert "rstudio" in component_keys
        assert "cellxgene" in component_keys
        assert "qc_dashboard" in component_keys
        assert "meilisearch" in component_keys

        # Verify full names - no "K8s" abbreviation
        for comp in data["components"]:
            assert "K8s" not in comp["name"], f"Component name should not contain 'K8s': {comp['name']}"

    @pytest.mark.asyncio
    async def test_snakemake_listed_as_coming_soon(self, client, admin_token, session):
        """Snakemake has no backend implementation yet so it must be coming_soon."""
        await _set_config(session, "compute_stack", "kubernetes")
        await _set_config(session, "compute_deployed", "true")
        await _set_config(session, "storage_deployed", "true")
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/stack/components",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()

        snakemake = next((c for c in data["components"] if c["key"] == "snakemake"), None)
        assert snakemake is not None
        assert snakemake["status"] == "coming_soon"

        # Nextflow should still be toggleable (disabled, not coming_soon)
        nextflow = next((c for c in data["components"] if c["key"] == "nextflow"), None)
        assert nextflow is not None
        assert nextflow["status"] != "coming_soon"


class TestComponentToggleEndpoint:
    @pytest.mark.asyncio
    async def test_component_toggle_enforces_dependencies(self, client, admin_token, session):
        """Test 20: Cannot enable Nextflow without kubernetes_cluster enabled."""
        await _set_config(session, "compute_stack", "kubernetes")
        await _set_config(session, "compute_deployed", "true")
        await _ensure_component(session, "kubernetes_cluster", enabled=False)
        await _ensure_component(session, "nextflow", enabled=False)
        await session.commit()

        response = await client.post(
            "/api/v1/infrastructure/stack/components/nextflow/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400
        assert "dependency" in response.json()["detail"].lower() or "kubernetes" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_component_toggle_updates_state(self, client, admin_token, session):
        """Test 21: Toggle enables component when dependencies met."""
        await _set_config(session, "compute_stack", "kubernetes")
        await _set_config(session, "compute_deployed", "true")
        await _ensure_component(session, "kubernetes_cluster", enabled=True, status="running")
        await _ensure_component(session, "nextflow", enabled=False)
        await session.commit()

        response = await client.post(
            "/api/v1/infrastructure/stack/components/nextflow/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True

        # Toggle again to disable
        response = await client.post(
            "/api/v1/infrastructure/stack/components/nextflow/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False


# -----------------------------------------------------------------------
# Cluster config
# -----------------------------------------------------------------------


class TestClusterConfigEndpoint:
    @pytest.mark.asyncio
    async def test_cluster_config_returns_current_values(self, client, admin_token, session):
        """Test 22: Config returns current machine types and node counts."""
        await _set_config(session, "k8s_pipeline_machine_type", "n2-highmem-16")
        await _set_config(session, "k8s_pipeline_max_nodes", "20")
        await _set_config(session, "k8s_pipeline_use_spot", "true")
        await _set_config(session, "k8s_interactive_machine_type", "n2-standard-4")
        await _set_config(session, "k8s_interactive_max_nodes", "5")
        await session.commit()

        response = await client.get(
            "/api/v1/infrastructure/cluster/config",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["k8s_pipeline_machine_type"] == "n2-highmem-16"
        assert data["k8s_pipeline_max_nodes"] == 20
        assert data["k8s_pipeline_use_spot"] is True
        assert data["k8s_interactive_machine_type"] == "n2-standard-4"
        assert data["k8s_interactive_max_nodes"] == 5

    @pytest.mark.asyncio
    async def test_cluster_config_update_auto_applies(self, client, admin_token, session):
        """Test 23: Config update plans and auto-applies."""
        await _set_config(session, "compute_deployed", "true")
        await _set_config(session, "compute_stack", "kubernetes")
        await session.commit()

        mock_run = MagicMock()
        mock_run.id = 99
        mock_run.status = "awaiting_confirmation"
        mock_run.plan_json = {
            "total": 1,
            "add_count": 0,
            "change_count": 1,
            "destroy_count": 0,
            "resources": [
                {
                    "type": "google_container_node_pool",
                    "name": "pipeline",
                    "address": "google_container_node_pool.pipeline",
                    "action": "update",
                },
            ],
        }
        mock_run.resources_planned = 1

        with (
            patch("app.api.stack_deploy.TerraformExecutor.run_plan", new_callable=AsyncMock) as mock_plan,
            patch("app.api.stack_deploy._run_apply_background", new_callable=AsyncMock),
        ):
            mock_plan.return_value = mock_run
            response = await client.post(
                "/api/v1/infrastructure/cluster/config",
                json={
                    "k8s_pipeline_max_nodes": 10,
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == 99
        assert data["message"] == "Cluster configuration update started"
        assert mock_run.status == "applying"


# -----------------------------------------------------------------------
# Sync compute config
# -----------------------------------------------------------------------


class TestSyncComputeConfigEndpoint:
    @pytest.mark.asyncio
    async def test_sync_compute_config_returns_populated(self, client, admin_token, session):
        """POST /sync-compute-config returns populated keys."""
        await _set_config(session, "compute_deployed", "true")
        await session.commit()

        with patch("app.api.stack_deploy.sync_compute_config") as mock_sync:
            from unittest.mock import AsyncMock

            mock_sync.side_effect = AsyncMock(
                return_value={
                    "gke_cluster_endpoint": "10.0.0.1",
                    "gke_cluster_ca_cert": "Y2VydA==",
                    "gke_cluster_name": "bioaf-test",
                }
            )
            response = await client.post(
                "/api/v1/infrastructure/stack/sync-compute-config",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "gke_cluster_endpoint" in data["populated"]

    @pytest.mark.asyncio
    async def test_sync_compute_config_requires_admin(self, client, viewer_token, session):
        """Non-admin users get 403 on sync-compute-config."""
        response = await client.post(
            "/api/v1/infrastructure/stack/sync-compute-config",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403
