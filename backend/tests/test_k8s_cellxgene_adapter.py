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
        assert result.publication_id == 42

    @pytest.mark.asyncio
    async def test_deploy_returns_starting_status(self, adapter, mock_k8s):
        result = await adapter.deploy(1, "gs://bucket/data.h5ad", "Dataset")
        assert result.status == "starting"

    @pytest.mark.asyncio
    async def test_deploy_sets_pod_name(self, adapter, mock_k8s):
        result = await adapter.deploy(5, "gs://bucket/data.h5ad", "Dataset")
        assert result.provider_details["pod_name"] == "cellxgene-5"

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
        assert result.status == "stopped"

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
        assert result.status == "stopped"


class TestCellxgeneGetStatus:
    @pytest.mark.asyncio
    async def test_status_running(self, adapter, mock_k8s):
        mock_dep = MagicMock()
        mock_dep.status.ready_replicas = 1
        mock_k8s["apps"].read_namespaced_deployment_status.return_value = mock_dep

        result = await adapter.get_status(1)
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_status_starting(self, adapter, mock_k8s):
        mock_dep = MagicMock()
        mock_dep.status.ready_replicas = 0
        mock_k8s["apps"].read_namespaced_deployment_status.return_value = mock_dep

        result = await adapter.get_status(1)
        assert result.status == "starting"

    @pytest.mark.asyncio
    async def test_status_unknown_on_error(self, adapter, mock_k8s):
        mock_k8s["apps"].read_namespaced_deployment_status.side_effect = Exception("gone")
        result = await adapter.get_status(999)
        assert result.status == "unknown"


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


import base64  # noqa: E402

_DUMMY_CA_PEM = (
    b"-----BEGIN CERTIFICATE-----\n"
    b"MIIBkTCB+wIJAJxYn4q3CmCgMA0GCSqGSIb3DQEBCwUAMBMxETAPBgNVBAMMCHRl\n"
    b"c3QtY2EwHhcNMjYwMTAxMDAwMDAwWhcNMjcwMTAxMDAwMDAwWjATMREwDwYDVQQD\n"
    b"DAh0ZXN0LWNhMIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDF7Z2KvfuWkLlj\n"
    b"-----END CERTIFICATE-----\n"
)


class TestCellxgeneOutOfClusterClientAuthHeader:
    """The cellxgene K8s client must install Authorization via default_headers.

    Mirrors backend/tests/test_k8s_notebook_adapter.py: Configuration.api_key
    is a silent no-op on the kubernetes-python release we ship, so the bearer
    token must be set via set_default_header. Without this, every cellxgene
    deployment poll would hit the cluster anonymously and 401.
    """

    def test_builds_client_with_authorization_default_header(self, monkeypatch):
        monkeypatch.delenv("BIOAF_COMPUTE_MODE", raising=False)
        provider = KubernetesCellxgeneProvider()
        provider._cluster_config = {
            "gke_cluster_endpoint": "1.2.3.4",
            "gke_cluster_ca_cert": base64.b64encode(_DUMMY_CA_PEM).decode(),
            "gcp_credential_source": "vm_default",
            "gcp_bootstrap_sa_email": "bioaf-bootstrap@example.iam.gserviceaccount.com",
        }
        fake_creds = MagicMock()
        fake_creds.token = "test-token-xyz"
        with patch(
            "app.platform.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ):
            api_client = provider._build_out_of_cluster_client()

        headers = api_client.default_headers
        auth_key = next((k for k in headers if k.lower() == "authorization"), None)
        assert auth_key is not None, (
            "ApiClient.default_headers has no Authorization entry; the kubernetes "
            "client will send anonymous requests and the cluster will 401."
        )
        assert headers[auth_key] == "Bearer test-token-xyz"


class TestCellxgeneClusterChangeRebuild:
    """A cluster teardown + redeploy (new endpoint/CA in platform_config) must
    invalidate cellxgene's cached K8s client. Without this, a still-valid GCP
    token keeps the adapter pointed at the dead cluster until the backend
    restarts, and every deploy/teardown silently 401s. Mirrors the notebook
    provider's cluster-change handling.
    """

    def _fake_load_cluster_config(self, provider, endpoint, ca_b64):
        async def _fake(force: bool = False):
            provider._cluster_config = {
                "gke_cluster_endpoint": endpoint,
                "gke_cluster_ca_cert": ca_b64,
                "gcp_credential_source": "vm_default",
                "gcp_bootstrap_sa_email": "bioaf-bootstrap@example.iam.gserviceaccount.com",
            }
            return provider._cluster_config

        return _fake

    def _setup(self, monkeypatch, token="tok"):
        from app.adapters.kubernetes import connection as conn_mod
        from app.platform import credential_injector as ci_mod

        monkeypatch.setattr(
            conn_mod.config,
            "load_incluster_config",
            MagicMock(side_effect=RuntimeError("not in cluster")),
        )
        fake_creds = MagicMock()
        fake_creds.token = token
        monkeypatch.setattr(ci_mod, "load_gcp_credentials", lambda *_: fake_creds)
        return fake_creds

    @pytest.mark.asyncio
    async def test_cluster_endpoint_change_rebuilds_client(self, monkeypatch):
        monkeypatch.delenv("BIOAF_COMPUTE_MODE", raising=False)
        provider = KubernetesCellxgeneProvider()
        fake_creds = self._setup(monkeypatch, token="tok-A")
        ca_b64 = base64.b64encode(_DUMMY_CA_PEM).decode()

        monkeypatch.setattr(
            provider._gke, "load_cluster_config", self._fake_load_cluster_config(provider, "1.1.1.1", ca_b64)
        )
        client_a = await provider._get_api_client_async()
        assert client_a.configuration.host == "https://1.1.1.1"

        monkeypatch.setattr(
            provider._gke, "load_cluster_config", self._fake_load_cluster_config(provider, "2.2.2.2", ca_b64)
        )
        fake_creds.token = "tok-B"
        client_b = await provider._get_api_client_async()
        assert client_b.configuration.host == "https://2.2.2.2", (
            "Cellxgene reused its cached K8s client after the cluster endpoint "
            "changed in platform_config. After a cluster teardown + redeploy this "
            "401s every deploy/teardown until the backend restarts."
        )

    @pytest.mark.asyncio
    async def test_unchanged_cluster_reuses_cached_client(self, monkeypatch):
        monkeypatch.delenv("BIOAF_COMPUTE_MODE", raising=False)
        provider = KubernetesCellxgeneProvider()
        self._setup(monkeypatch)
        ca_b64 = base64.b64encode(_DUMMY_CA_PEM).decode()
        monkeypatch.setattr(
            provider._gke, "load_cluster_config", self._fake_load_cluster_config(provider, "9.9.9.9", ca_b64)
        )

        first = await provider._get_api_client_async()
        second = await provider._get_api_client_async()
        assert first is second
