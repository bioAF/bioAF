"""Tests for the Kubernetes notebook adapter in local mode."""

import pytest

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider, _local_sessions


@pytest.fixture(autouse=True)
def set_local_mode(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")


@pytest.fixture(autouse=True)
def clear_sessions():
    _local_sessions.clear()
    yield
    _local_sessions.clear()


@pytest.fixture
def adapter():
    return KubernetesNotebookProvider()


class TestNotebookLaunchSession:
    @pytest.mark.asyncio
    async def test_launch_returns_session_id(self, adapter):
        result = await adapter.launch_session({"session_type": "jupyter", "resource_profile": "small"})
        assert "session_id" in result
        assert result["session_id"].startswith("local-")

    @pytest.mark.asyncio
    async def test_launch_returns_running_status(self, adapter):
        result = await adapter.launch_session({"session_type": "jupyter"})
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_launch_returns_url(self, adapter):
        result = await adapter.launch_session({"session_type": "jupyter"})
        assert "url" in result
        assert "8888" in result["url"]

    @pytest.mark.asyncio
    async def test_launch_rstudio_url(self, adapter):
        result = await adapter.launch_session({"session_type": "rstudio"})
        assert "8787" in result["url"]

    @pytest.mark.asyncio
    async def test_launch_stores_in_local_sessions(self, adapter):
        result = await adapter.launch_session({"session_type": "jupyter"})
        assert result["session_id"] in _local_sessions


class TestNotebookTerminateSession:
    @pytest.mark.asyncio
    async def test_terminate_updates_status(self, adapter):
        launched = await adapter.launch_session({"session_type": "jupyter"})
        result = await adapter.terminate_session(launched["session_id"])
        assert result["status"] == "stopped"
        assert "stopped_at" in result

    @pytest.mark.asyncio
    async def test_terminate_updates_local_store(self, adapter):
        launched = await adapter.launch_session({"session_type": "jupyter"})
        await adapter.terminate_session(launched["session_id"])
        assert _local_sessions[launched["session_id"]]["status"] == "stopped"


class TestNotebookSessionStatus:
    @pytest.mark.asyncio
    async def test_status_of_running_session(self, adapter):
        launched = await adapter.launch_session({"session_type": "jupyter"})
        result = await adapter.get_session_status(launched["session_id"])
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_status_of_unknown_session(self, adapter):
        result = await adapter.get_session_status("nonexistent-id")
        assert result["status"] == "unknown"


class TestNotebookListSessions:
    @pytest.mark.asyncio
    async def test_list_empty(self, adapter):
        result = await adapter.list_sessions()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_after_launch(self, adapter):
        await adapter.launch_session({"session_type": "jupyter"})
        await adapter.launch_session({"session_type": "rstudio"})
        result = await adapter.list_sessions()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_with_type_filter(self, adapter):
        await adapter.launch_session({"session_type": "jupyter"})
        await adapter.launch_session({"session_type": "rstudio"})
        result = await adapter.list_sessions({"session_type": "jupyter"})
        assert len(result) == 1
        assert result[0]["session_type"] == "jupyter"


class TestNotebookConnectionCommand:
    @pytest.mark.asyncio
    async def test_connection_command_format(self, adapter):
        cmd = await adapter.get_connection_command("abc123")
        assert "kubectl exec" in cmd
        assert "bioaf-notebooks" in cmd
        assert "bioaf-notebook-abc123" in cmd


# ---------------------------------------------------------------------------
# Out-of-cluster K8s client authentication and cache invalidation.
#
# Regression: the kubernetes-python ApiClient does not pick up bearer tokens
# from `Configuration.api_key` unless an OpenAPI security scheme references
# that api_key entry. Setting `api_key={"authorization": "Bearer <token>"}`
# silently sends no Authorization header -> the cluster 401s every request.
# These tests pin the contract that out-of-cluster clients must put the token
# in the request via `set_default_header`, and that the cached client is
# invalidated when the cluster identity in platform_config changes.
# ---------------------------------------------------------------------------

import base64  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

# A throwaway self-signed PEM is enough; `_build_out_of_cluster_client`
# only writes it to a temp file and hands the path to the client config.
# The bytes are never verified during construction.
_DUMMY_CA_PEM = (
    b"-----BEGIN CERTIFICATE-----\n"
    b"MIIBkTCB+wIJAJxYn4q3CmCgMA0GCSqGSIb3DQEBCwUAMBMxETAPBgNVBAMMCHRl\n"
    b"c3QtY2EwHhcNMjYwMTAxMDAwMDAwWhcNMjcwMTAxMDAwMDAwWjATMREwDwYDVQQD\n"
    b"DAh0ZXN0LWNhMIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDF7Z2KvfuWkLlj\n"
    b"-----END CERTIFICATE-----\n"
)
_DUMMY_CA_B64 = base64.b64encode(_DUMMY_CA_PEM).decode()


class TestOutOfClusterClientAuthHeader:
    """The K8s client built from platform_config must send Authorization."""

    @pytest.fixture
    def provider_with_cfg(self, monkeypatch):
        # Force the out-of-cluster path (no BIOAF_COMPUTE_MODE=local override
        # is needed for _build_out_of_cluster_client itself, but make the
        # branch obvious).
        monkeypatch.delenv("BIOAF_COMPUTE_MODE", raising=False)
        p = KubernetesNotebookProvider()
        p._cluster_config = {
            "gke_cluster_endpoint": "1.2.3.4",
            "gke_cluster_ca_cert": _DUMMY_CA_B64,
            "gcp_credential_source": "vm_default",
            "gcp_bootstrap_sa_email": "bioaf-bootstrap@example.iam.gserviceaccount.com",
        }
        return p

    def test_builds_client_with_authorization_default_header(self, provider_with_cfg):
        """Returned ApiClient must carry Authorization: Bearer <token>."""
        fake_creds = MagicMock()
        fake_creds.token = "test-token-xyz"
        with patch(
            "app.platform.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ):
            api_client = provider_with_cfg._build_out_of_cluster_client()

        headers = api_client.default_headers
        auth_key = next((k for k in headers if k.lower() == "authorization"), None)
        assert auth_key is not None, (
            "ApiClient.default_headers has no Authorization entry; the kubernetes "
            "client will send anonymous requests and the cluster will 401. "
            "Use set_default_header('Authorization', ...), not configuration.api_key."
        )
        assert headers[auth_key] == "Bearer test-token-xyz"

    def test_endpoint_normalized_to_https_url(self, provider_with_cfg):
        fake_creds = MagicMock()
        fake_creds.token = "t"
        with patch(
            "app.platform.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ):
            api_client = provider_with_cfg._build_out_of_cluster_client()
        assert api_client.configuration.host == "https://1.2.3.4"

    def test_httpx_reader_path_can_retrieve_auth_header(self, provider_with_cfg):
        """The LB-IP polling path reads the Authorization header from the
        ApiClient to forward to httpx. It must be present after
        _build_out_of_cluster_client; an IndexError or KeyError here breaks
        the access_url discovery for every notebook session.
        """
        fake_creds = MagicMock()
        fake_creds.token = "test-token-xyz"
        with patch(
            "app.platform.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ):
            api_client = provider_with_cfg._build_out_of_cluster_client()

        auth = api_client.default_headers.get("Authorization")
        assert auth is not None
        assert auth.startswith("Bearer ")
        assert "test-token-xyz" in auth


class TestApiClientCacheInvalidation:
    """Rebuilding the cluster must invalidate any cached K8s client."""

    @pytest.fixture
    def provider(self, monkeypatch):
        monkeypatch.delenv("BIOAF_COMPUTE_MODE", raising=False)
        return KubernetesNotebookProvider()

    def _fake_load_cluster_config(self, provider, endpoint, ca_b64):
        """Stand-in for load_cluster_config that just sets _cluster_config."""

        async def _fake(force: bool = False):
            provider._cluster_config = {
                "gke_cluster_endpoint": endpoint,
                "gke_cluster_ca_cert": ca_b64,
                "gcp_credential_source": "vm_default",
                "gcp_bootstrap_sa_email": "bioaf-bootstrap@example.iam.gserviceaccount.com",
            }
            return provider._cluster_config

        return _fake

    @pytest.mark.asyncio
    async def test_cluster_endpoint_change_invalidates_cached_client(self, provider, monkeypatch):
        """If platform_config now reports a different cluster, drop the cache."""
        # Force the out-of-cluster path: make load_incluster_config raise.
        from app.adapters.notebooks import kubernetes as kmod
        from app.platform import credential_injector as ci_mod

        monkeypatch.setattr(
            kmod.config,
            "load_incluster_config",
            MagicMock(side_effect=RuntimeError("not in cluster")),
        )

        fake_creds = MagicMock()
        fake_creds.token = "tok-A"
        monkeypatch.setattr(ci_mod, "load_gcp_credentials", lambda *_: fake_creds)

        # First call: cluster A.
        monkeypatch.setattr(
            provider,
            "load_cluster_config",
            self._fake_load_cluster_config(provider, "1.1.1.1", _DUMMY_CA_B64),
        )
        client_a = await provider._get_api_client_async()
        assert client_a.configuration.host == "https://1.1.1.1"

        # Now platform_config reports a different cluster (rebuild happened).
        # The cached _api_client must NOT be returned: we want a fresh build
        # against the new endpoint.
        monkeypatch.setattr(
            provider,
            "load_cluster_config",
            self._fake_load_cluster_config(provider, "2.2.2.2", _DUMMY_CA_B64),
        )
        fake_creds.token = "tok-B"
        client_b = await provider._get_api_client_async()
        assert client_b.configuration.host == "https://2.2.2.2", (
            "Cached K8s client was reused after cluster endpoint changed in "
            "platform_config. This will silently 401 every request after a "
            "cluster rebuild until the GCP access token TTL expires."
        )

    @pytest.mark.asyncio
    async def test_unchanged_cluster_reuses_cached_client(self, provider, monkeypatch):
        """Don't churn the client when nothing relevant changed."""
        from app.adapters.notebooks import kubernetes as kmod
        from app.platform import credential_injector as ci_mod

        monkeypatch.setattr(
            kmod.config,
            "load_incluster_config",
            MagicMock(side_effect=RuntimeError("not in cluster")),
        )
        fake_creds = MagicMock()
        fake_creds.token = "tok"
        monkeypatch.setattr(ci_mod, "load_gcp_credentials", lambda *_: fake_creds)
        monkeypatch.setattr(
            provider,
            "load_cluster_config",
            self._fake_load_cluster_config(provider, "9.9.9.9", _DUMMY_CA_B64),
        )

        first = await provider._get_api_client_async()
        second = await provider._get_api_client_async()
        assert first is second
