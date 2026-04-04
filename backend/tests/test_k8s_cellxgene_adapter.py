"""Tests for the Kubernetes cellxgene adapter.

Covers deploy/teardown/status (with mocked K8s clients) and namespace setup.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.cellxgene.kubernetes import KubernetesCellxgeneProvider


@pytest.fixture
def adapter():
    provider = KubernetesCellxgeneProvider()
    # Pre-populate cluster config so _get_api_client_async won't hit the DB
    provider._cluster_config = {
        "gke_cluster_endpoint": "https://10.0.0.1",
        "gke_cluster_ca_cert": "",
        "gcp_service_account_key": "",
    }
    return provider


@pytest.fixture
def mock_k8s(adapter):
    """Patch all K8s client accessors with mocks."""
    mock_apps = MagicMock()
    mock_core = MagicMock()
    mock_rbac = MagicMock()

    with (
        patch.object(adapter, "_get_api_client_async", new_callable=AsyncMock),
        patch.object(
            adapter,
            "_resolve_image",
            new_callable=AsyncMock,
            return_value="us-central1-docker.pkg.dev/p/r/bioaf-cellxgene:latest",
        ),
        patch.object(adapter, "_ensure_gcp_secret", new_callable=AsyncMock),
        patch.object(adapter, "_get_k8s_apps_client", return_value=mock_apps),
        patch.object(adapter, "_get_k8s_core_client", return_value=mock_core),
        patch.object(adapter, "_get_k8s_rbac_client", return_value=mock_rbac),
        patch("asyncio.create_task"),
    ):
        # Namespace already exists so ensure_cellxgene_namespace is a no-op
        adapter._namespace_ready = True
        yield {"apps": mock_apps, "core": mock_core, "rbac": mock_rbac}


class TestCellxgeneDeploy:
    @pytest.mark.asyncio
    async def test_deploy_returns_publication_id(self, adapter, mock_k8s):
        result = await adapter.deploy(42, "gs://bucket/data.h5ad", "My Dataset")
        assert result["publication_id"] == 42

    @pytest.mark.asyncio
    async def test_deploy_returns_starting_status(self, adapter, mock_k8s):
        result = await adapter.deploy(1, "gs://bucket/data.h5ad", "Dataset")
        assert result["status"] == "starting"

    @pytest.mark.asyncio
    async def test_deploy_sets_pod_name(self, adapter, mock_k8s):
        result = await adapter.deploy(5, "gs://bucket/data.h5ad", "Dataset")
        assert result["pod_name"] == "cellxgene-5"

    @pytest.mark.asyncio
    async def test_deploy_creates_deployment_and_service(self, adapter, mock_k8s):
        await adapter.deploy(1, "gs://bucket/data.h5ad", "Dataset")
        mock_k8s["apps"].create_namespaced_deployment.assert_called_once()
        mock_k8s["core"].create_namespaced_service.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_uses_correct_namespace(self, adapter, mock_k8s):
        await adapter.deploy(1, "gs://bucket/data.h5ad", "Dataset")
        call_kwargs = mock_k8s["apps"].create_namespaced_deployment.call_args[1]
        assert call_kwargs["namespace"] == "bioaf-cellxgene"

    @pytest.mark.asyncio
    async def test_deploy_creates_loadbalancer_service(self, adapter, mock_k8s):
        await adapter.deploy(1, "gs://bucket/data.h5ad", "Dataset")
        svc_body = mock_k8s["core"].create_namespaced_service.call_args[1]["body"]
        assert svc_body.spec.type == "LoadBalancer"

    @pytest.mark.asyncio
    async def test_deploy_uses_init_container_for_gcs_download(self, adapter, mock_k8s):
        await adapter.deploy(1, "gs://bucket/data.h5ad", "Dataset")
        dep_body = mock_k8s["apps"].create_namespaced_deployment.call_args[1]["body"]
        init_containers = dep_body.spec.template.spec.init_containers
        assert len(init_containers) == 1
        assert init_containers[0].name == "gcs-download"
        assert "gsutil cp" in init_containers[0].command[2]

    @pytest.mark.asyncio
    async def test_deploy_cellxgene_serves_local_path(self, adapter, mock_k8s):
        await adapter.deploy(1, "gs://bucket/data.h5ad", "Dataset")
        dep_body = mock_k8s["apps"].create_namespaced_deployment.call_args[1]["body"]
        main_container = dep_body.spec.template.spec.containers[0]
        assert "/data/dataset.h5ad" in main_container.args


class TestCellxgeneTeardown:
    @pytest.mark.asyncio
    async def test_teardown_returns_stopped(self, adapter, mock_k8s):
        result = await adapter.teardown(1)
        assert result["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_teardown_deletes_deployment_and_service(self, adapter, mock_k8s):
        await adapter.teardown(1)
        mock_k8s["apps"].delete_namespaced_deployment.assert_called_once_with(
            name="cellxgene-1", namespace="bioaf-cellxgene"
        )
        mock_k8s["core"].delete_namespaced_service.assert_called_once_with(
            name="cellxgene-1", namespace="bioaf-cellxgene"
        )

    @pytest.mark.asyncio
    async def test_teardown_tolerates_missing_resources(self, adapter, mock_k8s):
        from kubernetes.client.rest import ApiException

        mock_k8s["apps"].delete_namespaced_deployment.side_effect = ApiException(status=404)
        mock_k8s["core"].delete_namespaced_service.side_effect = ApiException(status=404)
        result = await adapter.teardown(999)
        assert result["status"] == "stopped"


class TestCellxgeneGetStatus:
    @pytest.mark.asyncio
    async def test_status_running(self, adapter, mock_k8s):
        mock_dep = MagicMock()
        mock_dep.status.ready_replicas = 1
        mock_k8s["apps"].read_namespaced_deployment_status.return_value = mock_dep

        result = await adapter.get_status(1)
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_status_starting(self, adapter, mock_k8s):
        mock_dep = MagicMock()
        mock_dep.status.ready_replicas = 0
        mock_k8s["apps"].read_namespaced_deployment_status.return_value = mock_dep

        result = await adapter.get_status(1)
        assert result["status"] == "starting"

    @pytest.mark.asyncio
    async def test_status_unknown_on_error(self, adapter, mock_k8s):
        mock_k8s["apps"].read_namespaced_deployment_status.side_effect = Exception("gone")
        result = await adapter.get_status(999)
        assert result["status"] == "unknown"


class TestCellxgeneNamespaceSetup:
    @pytest.fixture
    def fresh_adapter(self):
        provider = KubernetesCellxgeneProvider()
        provider._cluster_config = {
            "gke_cluster_endpoint": "https://10.0.0.1",
        }
        return provider

    @pytest.mark.asyncio
    async def test_creates_namespace_sa_and_rolebinding(self, fresh_adapter):
        mock_core_v1 = MagicMock()
        mock_rbac_v1 = MagicMock()

        from kubernetes.client.rest import ApiException

        mock_core_v1.read_namespace.side_effect = ApiException(status=404)

        with patch.object(fresh_adapter, "_get_k8s_core_client", return_value=mock_core_v1):
            with patch.object(fresh_adapter, "_get_k8s_rbac_client", return_value=mock_rbac_v1):
                await fresh_adapter.ensure_cellxgene_namespace()

        mock_core_v1.create_namespace.assert_called_once()
        mock_core_v1.create_namespaced_service_account.assert_called_once()
        mock_rbac_v1.create_namespaced_role_binding.assert_called_once()

        ns_body = mock_core_v1.create_namespace.call_args[1]["body"]
        assert ns_body.metadata.name == "bioaf-cellxgene"

        sa_call = mock_core_v1.create_namespaced_service_account.call_args
        assert sa_call[1]["namespace"] == "bioaf-cellxgene"
        assert sa_call[1]["body"].metadata.name == "bioaf-cellxgene-runner"

    @pytest.mark.asyncio
    async def test_skips_creation_if_namespace_exists(self, fresh_adapter):
        mock_core_v1 = MagicMock()
        mock_rbac_v1 = MagicMock()

        mock_core_v1.read_namespace.return_value = MagicMock()

        with patch.object(fresh_adapter, "_get_k8s_core_client", return_value=mock_core_v1):
            with patch.object(fresh_adapter, "_get_k8s_rbac_client", return_value=mock_rbac_v1):
                await fresh_adapter.ensure_cellxgene_namespace()

        mock_core_v1.create_namespace.assert_not_called()
        mock_core_v1.create_namespaced_service_account.assert_not_called()
        mock_rbac_v1.create_namespaced_role_binding.assert_not_called()
