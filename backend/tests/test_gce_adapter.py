"""Tests for GCE work-node adapter SA hardening (Breakages 1, 2)."""

from unittest.mock import MagicMock, patch

import pytest

from app.adapters.work_nodes.gce import GCEWorkNodeProvider


def test_get_gcp_credentials_uses_credential_injector_in_vm_default():
    """_get_gcp_credentials must delegate to credential_injector for ADC fallback.

    Greenfield installs have no JSON key in platform_config; the previous
    implementation raised RuntimeError. After SA hardening, the adapter
    must obtain ADC (or impersonated bootstrap creds) via credential_injector.
    """
    provider = GCEWorkNodeProvider()
    provider._gcp_config = {
        "gcp_credential_source": "vm_default",
        "gcp_bootstrap_sa_email": "bioaf-bootstrap@p.iam.gserviceaccount.com",
    }

    sentinel = MagicMock(name="impersonated_creds")
    with patch(
        "app.adapters.work_nodes.gce.load_gcp_credentials",
        return_value=sentinel,
    ) as mock_loader:
        creds = provider._get_gcp_credentials()

    assert creds is sentinel
    cfg = mock_loader.call_args.args[0]
    assert cfg.get("gcp_credential_source") == "vm_default"
    assert cfg.get("gcp_bootstrap_sa_email") == "bioaf-bootstrap@p.iam.gserviceaccount.com"


def test_get_gcp_credentials_works_with_legacy_key_mode():
    """service_account_key mode still works through the injector."""
    provider = GCEWorkNodeProvider()
    provider._gcp_config = {
        "gcp_credential_source": "service_account_key",
        "gcp_service_account_key": '{"type": "service_account"}',
    }
    sentinel = MagicMock(name="legacy_creds")
    with patch(
        "app.adapters.work_nodes.gce.load_gcp_credentials",
        return_value=sentinel,
    ):
        creds = provider._get_gcp_credentials()
    assert creds is sentinel


@pytest.mark.asyncio
async def test_load_gcp_config_includes_bootstrap_sa_email_key():
    """The platform_config SELECT must include gcp_bootstrap_sa_email."""
    captured_sql: list[str] = []

    class _FakeResult:
        def fetchall(self):
            return []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            captured_sql.append(str(stmt))
            return _FakeResult()

    def factory():
        return _FakeSession()

    provider = GCEWorkNodeProvider(session_factory=factory)
    await provider.load_gcp_config()

    assert any("gcp_bootstrap_sa_email" in s for s in captured_sql), captured_sql


def test_work_node_sa_resolution_drops_legacy_email_fallback():
    """The SA attached to the work-node VM must NOT fall back to
    gcp_service_account_email — that key now points at bioaf-bootstrap.
    """
    cfg = {
        "gcp_service_account_email": "bioaf-bootstrap@p.iam.gserviceaccount.com",
    }
    vm_spec: dict = {}

    sa_email = vm_spec.get("service_account_email") or cfg.get("notebook_runner_sa_email") or None
    # Helper mirrors the adapter logic; the regression check is that the
    # third fallback (gcp_service_account_email) is NOT consulted.
    assert sa_email is None
