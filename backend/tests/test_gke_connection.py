"""Unit tests for the shared GkeConnection collaborator.

GkeConnection holds the GKE connect + auth plumbing that was previously
copy-pasted into the compute, notebook, and cellxgene Kubernetes providers.
These tests pin its behavior directly so the providers can delegate to one
tested implementation.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.kubernetes.connection import GkeConnection

# Minimal config key set; individual tests seed _cluster_config directly.
_KEYS = ["gke_cluster_endpoint", "gke_cluster_ca_cert", "gcp_credential_source"]
_CA_B64 = base64.b64encode(b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n").decode()


def _conn(**kwargs) -> GkeConnection:
    return GkeConnection(config_keys=_KEYS, **kwargs)


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
