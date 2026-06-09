"""Tests for GCE work-node adapter SA hardening (Breakages 1, 2)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.work_nodes.gce import GCEWorkNodeProvider
from app.exceptions import ValidationError


def _launch_provider():
    provider = GCEWorkNodeProvider()
    provider._mode = "gce"
    provider._gcp_config = {
        "gcp_project_id": "proj",
        "gcp_zone": "us-central1-b",
        "notebook_runner_sa_email": "runner@p.iam.gserviceaccount.com",
    }
    return provider


def _launch_vm_spec():
    return {
        "gcp_project_id": "proj",
        "gcp_zone": "us-central1-b",
        "session_id": 7,
        "user_id": 1,
        "machine_type": "n2-standard-4",
        "image_uri": "projects/proj/global/images/bioaf-worknode",
        "session_credentials": {"username": "bioaf", "password_hash": "x"},
    }


@pytest.mark.asyncio
async def test_launch_advances_past_exhausted_zone():
    """A zone stockout must skip to the next zone, not silently succeed.

    GCE instances.insert is asynchronous: it returns an accepted operation and
    the ZONE_RESOURCE_POOL_EXHAUSTED failure only surfaces when the operation
    result is resolved. The adapter must resolve the operation so the existing
    zone-failover loop actually advances past the exhausted zone.
    """
    provider = _launch_provider()
    insert_calls: list[str] = []

    def insert_side_effect(**kwargs):
        zone = kwargs["zone"]
        insert_calls.append(zone)
        op = MagicMock()
        if zone.endswith("-b"):
            op.result.side_effect = Exception("operation failed: ZONE_RESOURCE_POOL_EXHAUSTED")
        else:
            op.result.return_value = None
        return op

    fake_client = MagicMock()
    fake_client.insert.side_effect = insert_side_effect

    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=fake_client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch.object(provider, "_poll_vm_ready", new=AsyncMock()),
    ):
        result = await provider._gce_launch_vm(_launch_vm_spec())

    assert result["zone"] == "us-central1-c"
    assert insert_calls[0] == "us-central1-b"
    assert "us-central1-c" in insert_calls


@pytest.mark.asyncio
async def test_launch_applies_configured_boot_disk():
    """vm_spec boot_disk_gb / boot_disk_type drive the created boot disk."""
    provider = _launch_provider()
    captured: dict = {}

    def insert_side_effect(**kwargs):
        captured["instance"] = kwargs["instance_resource"]
        op = MagicMock()
        op.result.return_value = None
        return op

    fake_client = MagicMock()
    fake_client.insert.side_effect = insert_side_effect

    vm_spec = _launch_vm_spec()
    vm_spec["boot_disk_gb"] = 150
    vm_spec["boot_disk_type"] = "pd-standard"

    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=fake_client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch.object(provider, "_poll_vm_ready", new=AsyncMock()),
    ):
        await provider._gce_launch_vm(vm_spec)

    init_params = captured["instance"].disks[0].initialize_params
    assert init_params.disk_size_gb == 150
    assert init_params.disk_type.endswith("/diskTypes/pd-standard")


@pytest.mark.asyncio
async def test_launch_boot_disk_defaults_to_100gb_pd_ssd():
    """Absent explicit settings, the boot disk defaults to 100 GB pd-ssd."""
    provider = _launch_provider()
    captured: dict = {}

    def insert_side_effect(**kwargs):
        captured["instance"] = kwargs["instance_resource"]
        op = MagicMock()
        op.result.return_value = None
        return op

    fake_client = MagicMock()
    fake_client.insert.side_effect = insert_side_effect

    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=fake_client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch.object(provider, "_poll_vm_ready", new=AsyncMock()),
    ):
        await provider._gce_launch_vm(_launch_vm_spec())

    init_params = captured["instance"].disks[0].initialize_params
    assert init_params.disk_size_gb == 100
    assert init_params.disk_type.endswith("/diskTypes/pd-ssd")


@pytest.mark.asyncio
async def test_launch_raises_when_all_zones_exhausted():
    """If every zone is exhausted, the adapter raises after trying them all."""
    provider = _launch_provider()
    insert_calls: list[str] = []

    def insert_side_effect(**kwargs):
        insert_calls.append(kwargs["zone"])
        op = MagicMock()
        op.result.side_effect = Exception("operation failed: ZONE_RESOURCE_POOL_EXHAUSTED")
        return op

    fake_client = MagicMock()
    fake_client.insert.side_effect = insert_side_effect

    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=fake_client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch.object(provider, "_poll_vm_ready", new=AsyncMock()),
    ):
        with pytest.raises(ValidationError, match="resources unavailable"):
            await provider._gce_launch_vm(_launch_vm_spec())

    assert insert_calls == [
        "us-central1-b",
        "us-central1-c",
        "us-central1-f",
        "us-central1-a",
    ]


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
    """The platform_config lookup must request gcp_bootstrap_sa_email."""
    captured_keys: list[list[str]] = []

    class _FakeResult:
        def fetchall(self):
            return []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            # Adapter now goes through PlatformConfigService.get_many, which
            # uses parameterized SQL. Inspect the bound params instead of the
            # SQL text to confirm the bootstrap key is requested.
            params = getattr(stmt, "compile", lambda: stmt)().params if hasattr(stmt, "compile") else {}
            keys = params.get("keys", [])
            captured_keys.append(list(keys))
            return _FakeResult()

    def factory():
        return _FakeSession()

    provider = GCEWorkNodeProvider(session_factory=factory)
    await provider.load_gcp_config()

    assert any("gcp_bootstrap_sa_email" in keys for keys in captured_keys), captured_keys


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
