"""Unit tests for the ClusterAuth seam (Stage 4a).

DB-free: the GKE provider's endpoint/CA resolution + token mint, and the factory
defaulting to GKE on a GCP/unconfigured install.
"""

from unittest.mock import MagicMock, patch

from app.adapters.cluster_auth import (
    DEFAULT_CLUSTER_AUTH_BACKEND,
    VALID_CLUSTER_AUTH_BACKENDS,
    create_cluster_auth_provider,
    get_cluster_auth_provider,
)
from app.adapters.cluster_auth.gcp import GkeClusterAuthProvider


def test_gke_provider_resolves_endpoint_and_ca_from_config():
    p = GkeClusterAuthProvider()
    cfg = {"gke_cluster_endpoint": "https://10.0.0.1", "gke_cluster_ca_cert": "Y2E="}
    assert p.cluster_endpoint(cfg) == "https://10.0.0.1"
    assert p.cluster_ca_cert(cfg) == "Y2E="
    # Missing keys resolve to empty string, not None.
    assert p.cluster_endpoint({}) == ""
    assert p.cluster_ca_cert({}) == ""


def test_gke_provider_token_ttl_is_under_gcp_token_life():
    assert GkeClusterAuthProvider().token_ttl_seconds == 2700


def test_gke_provider_mints_token_via_credential_injector():
    p = GkeClusterAuthProvider()
    fake_creds = MagicMock()
    fake_creds.token = "ya29.fake"
    cfg = {"gcp_credential_source": "vm_default"}
    with (
        patch(
            "app.adapters.credentials.credential_injector.load_gcp_credentials",
            return_value=fake_creds,
        ) as load,
        patch("google.auth.transport.requests.Request"),
    ):
        token = p.bearer_token(cfg)
    load.assert_called_once_with(cfg)
    fake_creds.refresh.assert_called_once()
    assert token == "ya29.fake"


def test_factory_defaults_to_gke():
    assert DEFAULT_CLUSTER_AUTH_BACKEND == "gke"
    assert "gke" in VALID_CLUSTER_AUTH_BACKENDS
    assert isinstance(create_cluster_auth_provider("gke"), GkeClusterAuthProvider)


def test_get_cluster_auth_provider_falls_back_to_gke_when_cache_unloaded():
    # backend_for('cluster_auth') falls back to the gcp policy default when the
    # resolved-backend cache is unloaded (tests / pre-DB), so this is GKE.
    from app.platform.cloud_provider import reset_resolved_backends

    reset_resolved_backends()
    assert isinstance(get_cluster_auth_provider(), GkeClusterAuthProvider)


def test_unknown_backend_raises():
    import pytest

    from app.exceptions import ValidationError

    with pytest.raises(ValidationError):
        create_cluster_auth_provider("eks")  # no EKS impl until Stage 6e
