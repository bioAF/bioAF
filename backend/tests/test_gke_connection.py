"""Unit tests for the shared GkeConnection collaborator.

GkeConnection holds the GKE connect + auth plumbing that was previously
copy-pasted into the compute, notebook, and cellxgene Kubernetes providers.
These tests pin its behavior directly so the providers can delegate to one
tested implementation.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.kubernetes.connection import GkeConnection

# Minimal config key set; individual tests seed _cluster_config directly.
_KEYS = ["gke_cluster_endpoint", "gke_cluster_ca_cert", "gcp_credential_source"]
_CA_B64 = base64.b64encode(b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n").decode()

_GET_MANY = "app.platform.platform_config_service.PlatformConfigService.get_many"


def _conn(**kwargs) -> GkeConnection:
    return GkeConnection(config_keys=_KEYS, **kwargs)


def _session_factory():
    """A session_factory whose () returns an async context manager (like AsyncSession)."""
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = MagicMock()
    mock_ctx.__aexit__.return_value = False
    return MagicMock(return_value=mock_ctx)


class TestTokenExpiry:
    def test_not_expired_when_client_never_built(self):
        """_client_created_at == 0.0 means no client yet, so not expired."""
        conn = _conn()
        assert conn.is_token_expired() is False

    def test_expired_after_ttl_elapsed(self, monkeypatch):
        conn = _conn()
        conn._client_created_at = 1000.0
        monkeypatch.setattr(
            "app.adapters.kubernetes.connection.time.monotonic",
            lambda: 1000.0 + conn._TOKEN_TTL_SECONDS + 1,
        )
        assert conn.is_token_expired() is True

    def test_not_expired_within_ttl(self, monkeypatch):
        conn = _conn()
        conn._client_created_at = 1000.0
        monkeypatch.setattr(
            "app.adapters.kubernetes.connection.time.monotonic",
            lambda: 1000.0 + 10,
        )
        assert conn.is_token_expired() is False


class TestBuildOutOfClusterClient:
    def _seed(self, conn, endpoint="1.2.3.4"):
        conn._cluster_config = {
            "gke_cluster_endpoint": endpoint,
            "gke_cluster_ca_cert": _CA_B64,
            "gcp_credential_source": "vm_default",
        }

    def test_installs_bearer_auth_via_default_header(self):
        """The bearer token must be on ApiClient.default_headers, not
        Configuration.api_key (which is a silent no-op in the shipped client,
        causing anonymous requests and 401s)."""
        conn = _conn()
        self._seed(conn)
        fake_creds = MagicMock()
        fake_creds.token = "tok-123"
        with patch(
            "app.platform.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ):
            api_client = conn.build_out_of_cluster_client()

        headers = api_client.default_headers
        auth_key = next((k for k in headers if k.lower() == "authorization"), None)
        assert auth_key is not None
        assert headers[auth_key] == "Bearer tok-123"

    def test_prepends_https_scheme_to_endpoint(self):
        conn = _conn()
        self._seed(conn, endpoint="1.2.3.4")
        fake_creds = MagicMock()
        fake_creds.token = "tok"
        with patch("app.platform.credential_injector.load_gcp_credentials", return_value=fake_creds):
            api_client = conn.build_out_of_cluster_client()
        assert api_client.configuration.host == "https://1.2.3.4"

    def test_preserves_existing_https_scheme(self):
        conn = _conn()
        self._seed(conn, endpoint="https://5.6.7.8")
        fake_creds = MagicMock()
        fake_creds.token = "tok"
        with patch("app.platform.credential_injector.load_gcp_credentials", return_value=fake_creds):
            api_client = conn.build_out_of_cluster_client()
        assert api_client.configuration.host == "https://5.6.7.8"

    def test_records_client_creation_time(self):
        conn = _conn()
        self._seed(conn)
        assert conn._client_created_at == 0.0
        fake_creds = MagicMock()
        fake_creds.token = "tok"
        with patch("app.platform.credential_injector.load_gcp_credentials", return_value=fake_creds):
            conn.build_out_of_cluster_client()
        assert conn._client_created_at > 0.0

    @pytest.mark.parametrize("bad_endpoint", ["", "null"])
    def test_raises_when_endpoint_missing_or_null(self, bad_endpoint):
        conn = _conn()
        conn._cluster_config = {
            "gke_cluster_endpoint": bad_endpoint,
            "gke_cluster_ca_cert": _CA_B64,
        }
        with pytest.raises(RuntimeError, match="endpoint"):
            conn.build_out_of_cluster_client()


class TestLoadClusterConfig:
    @pytest.mark.asyncio
    async def test_returns_empty_without_session_factory(self):
        conn = _conn(session_factory=None)
        cfg = await conn.load_cluster_config()
        assert cfg == {}
        assert conn._cluster_config == {}

    @pytest.mark.asyncio
    async def test_reads_exactly_the_configured_keys(self):
        conn = _conn(session_factory=_session_factory())
        with patch(_GET_MANY, new_callable=AsyncMock, return_value={"gke_cluster_endpoint": "https://1.2.3.4"}) as mock_get:
            cfg = await conn.load_cluster_config()
        assert cfg["gke_cluster_endpoint"] == "https://1.2.3.4"
        assert mock_get.call_args.args[1] == _KEYS

    @pytest.mark.asyncio
    async def test_caches_and_skips_reread_when_endpoint_present(self):
        conn = _conn(session_factory=_session_factory())
        conn._cluster_config = {"gke_cluster_endpoint": "https://cached"}
        with patch(_GET_MANY, new_callable=AsyncMock) as mock_get:
            cfg = await conn.load_cluster_config()
        mock_get.assert_not_called()
        assert cfg["gke_cluster_endpoint"] == "https://cached"

    @pytest.mark.asyncio
    async def test_rereads_when_cached_endpoint_is_null(self):
        conn = _conn(session_factory=_session_factory())
        conn._cluster_config = {"gke_cluster_endpoint": "null"}
        with patch(_GET_MANY, new_callable=AsyncMock, return_value={"gke_cluster_endpoint": "https://fresh"}) as mock_get:
            cfg = await conn.load_cluster_config()
        mock_get.assert_called_once()
        assert cfg["gke_cluster_endpoint"] == "https://fresh"

    @pytest.mark.asyncio
    async def test_force_rereads_even_when_endpoint_present(self):
        conn = _conn(session_factory=_session_factory())
        conn._cluster_config = {"gke_cluster_endpoint": "https://cached"}
        with patch(_GET_MANY, new_callable=AsyncMock, return_value={"gke_cluster_endpoint": "https://fresh"}) as mock_get:
            cfg = await conn.load_cluster_config(force=True)
        mock_get.assert_called_once()
        assert cfg["gke_cluster_endpoint"] == "https://fresh"

    @pytest.mark.asyncio
    async def test_force_invalidates_cached_client_when_enabled(self):
        conn = _conn(session_factory=_session_factory(), invalidate_client_on_force=True)
        conn._api_client = MagicMock()
        with patch(_GET_MANY, new_callable=AsyncMock, return_value={"gke_cluster_endpoint": "https://x"}):
            await conn.load_cluster_config(force=True)
        assert conn._api_client is None

    @pytest.mark.asyncio
    async def test_force_preserves_cached_client_when_disabled(self):
        conn = _conn(session_factory=_session_factory(), invalidate_client_on_force=False)
        sentinel = MagicMock()
        conn._api_client = sentinel
        with patch(_GET_MANY, new_callable=AsyncMock, return_value={"gke_cluster_endpoint": "https://x"}):
            await conn.load_cluster_config(force=True)
        assert conn._api_client is sentinel
