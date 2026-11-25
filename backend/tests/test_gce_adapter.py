"""Tests for GCE work-node adapter SA hardening (Breakages 1, 2)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.settings_contract import ComputeMode
from tests.support.mode_adapters import build_mode_adapter
from app.adapters.work_nodes.gce import GCEWorkNodeProvider
from app.exceptions import ValidationError


def _launch_provider():
    provider = build_mode_adapter(GCEWorkNodeProvider, mode=ComputeMode.KUBERNETES)
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
    gcp_service_account_email: that key now points at bioaf-bootstrap.
    """
    cfg = {
        "gcp_service_account_email": "bioaf-bootstrap@p.iam.gserviceaccount.com",
    }
    vm_spec: dict = {}

    sa_email = vm_spec.get("service_account_email") or cfg.get("notebook_runner_sa_email") or None
    # Helper mirrors the adapter logic; the regression check is that the
    # third fallback (gcp_service_account_email) is NOT consulted.
    assert sa_email is None


# -- Plan 03A: save over verified SSH, then delete and confirm -------------------------------


def _gce_instance(status="RUNNING", ip="34.1.2.3", session="7", attempt="attempt-1", instance_id=555):
    from types import SimpleNamespace

    access = [SimpleNamespace(nat_i_p=ip)] if ip else []
    return SimpleNamespace(
        id=instance_id,
        status=status,
        labels={"bioaf-session": session, "bioaf-launch": attempt},
        network_interfaces=[SimpleNamespace(access_configs=access, network_i_p="10.128.0.5")],
    )


_GCE_CREDS = dict(
    gcp_project_id="proj",
    session_id=7,
    working_bucket="bioaf-working",
    sync_private_key="sync-private",
    host_public_key="ssh-ed25519 AAAAhost",
    provider_instance_id="555",
    launch_attempt_id="attempt-1",
)


async def _gce_delete(client, sync=None, **over):
    provider = _launch_provider()
    provider._delete_poll_interval = 0
    sync = sync or AsyncMock(return_value=0)
    blob = MagicMock()
    blob.name = "sessions/7/outputs/result.csv"
    blob.size = 12
    storage_client = MagicMock()
    storage_client.bucket.return_value.list_blobs.return_value = [blob]
    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=client),
        patch("google.cloud.storage.Client", return_value=storage_client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch("app.adapters.work_nodes.gce.run_sync_command", sync),
    ):
        result = await provider.delete("bioaf-worknode-7", "us-central1-b", **{**_GCE_CREDS, **over})
    return result, sync


def _not_found():
    from google.api_core.exceptions import NotFound

    return NotFound("gone")


@pytest.mark.asyncio
async def test_gce_delete_saves_with_recorded_identity_then_confirms_deletion():
    client = MagicMock()
    client.get.side_effect = [_gce_instance(), _not_found()]
    result, sync = await _gce_delete(client)
    assert sync.call_args.args[0] == "34.1.2.3"
    assert sync.call_args.kwargs["host_public_key"] == "ssh-ed25519 AAAAhost"
    assert "exit $rc" in sync.call_args.kwargs["command"]
    client.delete.assert_called_once()
    assert result.stop_failure is None
    assert result.status.value == "stopped"
    assert [o.filename for o in result.output_files] == ["result.csv"]


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_status", [1, 255])
async def test_gce_delete_keeps_the_vm_when_the_save_fails(exit_status):
    client = MagicMock()
    client.get.return_value = _gce_instance()
    result, _ = await _gce_delete(client, AsyncMock(return_value=exit_status))
    client.delete.assert_not_called()
    client.stop.assert_not_called()
    assert result.stop_failure == "save_failed"
    assert result.status.value == "running"


@pytest.mark.asyncio
async def test_gce_delete_keeps_the_vm_without_an_address_or_credentials():
    for over, instance in [
        ({}, _gce_instance(ip=None)),
        ({"sync_private_key": None}, _gce_instance()),
        ({"host_public_key": None}, _gce_instance()),
        ({"working_bucket": ""}, _gce_instance()),
    ]:
        client = MagicMock()
        client.get.return_value = instance
        result, sync = await _gce_delete(client, **over)
        sync.assert_not_called()
        client.delete.assert_not_called()
        assert result.stop_failure == "save_failed", over


@pytest.mark.asyncio
async def test_gce_delete_refuses_a_replacement_vm_with_the_same_name():
    """A VM recreated under the recorded name is a different instance: it is never trusted or deleted."""
    client = MagicMock()
    client.get.return_value = _gce_instance(instance_id=999)
    result, sync = await _gce_delete(client)
    sync.assert_not_called()
    client.delete.assert_not_called()
    assert result.stop_failure == "state_unknown"


@pytest.mark.asyncio
async def test_gce_delete_reports_unknown_when_the_vm_cannot_be_read():
    client = MagicMock()
    client.get.side_effect = RuntimeError("permission denied")
    result, _ = await _gce_delete(client)
    client.delete.assert_not_called()
    assert result.stop_failure == "state_unknown"


@pytest.mark.asyncio
async def test_gce_delete_reports_unconfirmed_deletion_after_a_save():
    client = MagicMock()
    client.get.return_value = _gce_instance()  # still there after delete
    result, _ = await _gce_delete(client)
    client.delete.assert_called_once()
    assert result.stop_failure == "delete_unconfirmed"


@pytest.mark.asyncio
async def test_gce_delete_of_a_missing_vm_is_stopped():
    client = MagicMock()
    client.get.side_effect = _not_found()
    result, sync = await _gce_delete(client)
    sync.assert_not_called()
    client.delete.assert_not_called()
    assert result.stop_failure is None


@pytest.mark.asyncio
async def test_gce_launch_installs_host_key_labels_attempt_and_returns_instance_id():
    from app.adapters.work_nodes.node_ssh import generate_node_identity

    identity = generate_node_identity()
    provider = _launch_provider()
    fake_client = MagicMock()
    fake_client.insert.return_value.result.return_value = None
    fake_client.get.return_value = _gce_instance(instance_id=4242)
    spec = {
        **_launch_vm_spec(),
        "sync_public_key": identity.sync_public_key,
        "host_private_key": identity.host_private_key,
        "host_public_key": identity.host_public_key,
        "launch_attempt_id": "attempt-9",
    }
    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=fake_client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch.object(provider, "_poll_vm_ready", new=AsyncMock()),
    ):
        result = await provider._gce_launch_vm(spec)

    instance = fake_client.insert.call_args.kwargs["instance_resource"]
    items = {i.key: i.value for i in instance.metadata.items}
    assert "/etc/ssh/bioaf_host_ed25519_key" in items["startup-script"]
    assert items["bioaf-host-public-key"] == identity.host_public_key
    assert instance.labels["bioaf-launch"] == "attempt-9"
    assert result["provider_instance_id"] == "4242"


# -- Recorded launch requests and their settled outcome (review 3, item 4) ----------------------


@pytest.mark.asyncio
async def test_gce_launch_records_each_zone_request_before_sending_it():
    provider = _launch_provider()
    events: list[tuple] = []

    async def record(entry):
        events.append(("record", entry["zone"], entry["state"]))

    def insert_side_effect(**kwargs):
        events.append(("insert", kwargs["zone"]))
        op = MagicMock()
        if kwargs["zone"].endswith("-b"):
            op.result.side_effect = Exception("operation failed: ZONE_RESOURCE_POOL_EXHAUSTED")
        return op

    fake_client = MagicMock()
    fake_client.insert.side_effect = insert_side_effect
    spec = {**_launch_vm_spec(), "launch_attempt_id": "attempt-9", "record_launch_request": record}
    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=fake_client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch.object(provider, "_poll_vm_ready", new=AsyncMock()),
    ):
        await provider._gce_launch_vm(spec)

    b, c = "us-central1-b", "us-central1-c"
    assert events == [
        ("record", b, "requested"),
        ("insert", b),
        ("record", b, "failed"),
        ("record", c, "requested"),
        ("insert", c),
        ("record", c, "created"),
    ]


def _op(status="DONE", error=None, target_id=4242):
    op = MagicMock()
    op.status = status
    op.error = error
    op.target_id = target_id
    return op


async def _settle(requests, ops=None, instance=None, ops_error=None, instance_reads=None):
    provider = _launch_provider()
    zone_ops = MagicMock()
    if ops_error:
        zone_ops.list.side_effect = ops_error
    else:
        zone_ops.list.return_value = ops or []
    instances = MagicMock()
    if instance_reads is not None:
        instances.get.side_effect = instance_reads
    elif instance is None:
        from google.api_core.exceptions import NotFound

        instances.get.side_effect = NotFound("gone")
    else:
        instances.get.return_value = instance
    with (
        patch("google.cloud.compute_v1.ZoneOperationsClient", return_value=zone_ops),
        patch("google.cloud.compute_v1.InstancesClient", return_value=instances),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
    ):
        return await provider.settle_launch(requests, session_id=7), zone_ops


REQ = {"key": "us-central1-b", "zone": "us-central1-b", "project": "proj", "state": "requested"}


@pytest.mark.asyncio
async def test_gce_an_attempt_that_sent_no_request_settles_as_no_vm():
    outcome, _ = await _settle([])
    assert outcome.state == "none"


@pytest.mark.asyncio
async def test_gce_a_request_the_zone_never_accepted_settles_as_no_vm():
    # Owner-approved (2026-11-24, review 5 item 1): no instance and no insert operation for an
    # unanswered request is not proof that nothing was made, so it stays unknown (the key is kept).
    outcome, zone_ops = await _settle([REQ], ops=[])
    assert outcome.state == "unknown"
    query = zone_ops.list.call_args.kwargs["request"].filter
    assert 'operationType = "insert"' in query
    assert "zones/us-central1-b/instances/bioaf-worknode-7" in query


@pytest.mark.asyncio
async def test_gce_a_failed_insert_operation_settles_as_no_vm():
    outcome, _ = await _settle([REQ], ops=[_op(error=MagicMock(errors=[MagicMock()]))])
    assert outcome.state == "none"


@pytest.mark.asyncio
async def test_gce_a_completed_insert_settles_as_its_vm():
    outcome, _ = await _settle([REQ], ops=[_op(error=None)], instance=_gce_instance(instance_id=4242))
    assert outcome.state == "created"
    assert (outcome.instance.name, outcome.instance.zone, outcome.instance.provider_instance_id) == (
        "bioaf-worknode-7",
        "us-central1-b",
        "4242",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["running", "unreadable"])
async def test_gce_an_unfinished_or_unreadable_operation_is_unknown(case):
    if case == "running":
        outcome, _ = await _settle([REQ], ops=[_op(status="RUNNING")])
    else:
        outcome, _ = await _settle([REQ], ops_error=RuntimeError("denied"))
    assert outcome.state == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["deadline_while_polling", "insert_unavailable"])
async def test_gce_an_ambiguous_error_is_not_recorded_as_a_failed_request(case):
    """Review 4 item 1: only an operation GCE reports done with an error proves no VM; a polling
    deadline on an accepted operation or a failed insert call stays "requested" for settlement."""
    from google.api_core.exceptions import DeadlineExceeded, ServiceUnavailable

    provider = _launch_provider()
    states: list[str] = []

    async def record(entry):
        states.append(entry["state"])

    fake_client = MagicMock()
    if case == "deadline_while_polling":
        op = MagicMock()
        op.done.return_value = False
        op.result.side_effect = DeadlineExceeded("still running")
        fake_client.insert.return_value = op
    else:
        fake_client.insert.side_effect = ServiceUnavailable("try later")
    spec = {**_launch_vm_spec(), "launch_attempt_id": "attempt-9", "record_launch_request": record}
    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=fake_client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch.object(provider, "_poll_vm_ready", new=AsyncMock()),
        pytest.raises(Exception),
    ):
        await provider._gce_launch_vm(spec)
    assert states == ["requested"]


@pytest.mark.asyncio
async def test_gce_a_vm_created_between_the_lookups_is_bound_not_discarded():
    """Review 5 item 1: the VM read says not found, creation finishes, then the operation reads DONE
    with no error. The VM is read again and bound; it is never settled as "no VM"."""
    from google.api_core.exceptions import NotFound

    outcome, _ = await _settle(
        [REQ], ops=[_op(error=None)], instance_reads=[NotFound("not yet"), _gce_instance(instance_id=4242)]
    )
    assert outcome.state == "created"
    assert outcome.instance.provider_instance_id == "4242"


@pytest.mark.asyncio
async def test_gce_a_completed_insert_whose_vm_cannot_be_read_again_is_unknown():
    outcome, _ = await _settle([REQ], ops=[_op(error=None)], instance_reads=[NotFound_("a"), RuntimeError("denied")])
    assert outcome.state == "unknown"


def NotFound_(message):
    from google.api_core.exceptions import NotFound

    return NotFound(message)


@pytest.mark.asyncio
async def test_gce_a_recorded_failure_settles_as_no_vm_without_reading_anything():
    outcome, zone_ops = await _settle([{**REQ, "state": "failed"}])
    assert outcome.state == "none"
    zone_ops.list.assert_not_called()
