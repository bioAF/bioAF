"""Plan 03.5D: each provider reads a Work Node's report only from the instance it launched, over the
route it recorded, with the credentials it recorded. Provider SDKs are mocked; the SSH exchange is
covered against a real server in test_work_node_report.py.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.work_nodes.ec2 import Ec2WorkNodeProvider
from app.adapters.work_nodes.gce import GCEWorkNodeProvider
from app.adapters.work_nodes.node_ssh import NodeReport, NodeReportMalformed, NodeSyncError, NodeTrustError
from app.settings_contract import ComputeMode
from tests.support.mode_adapters import build_mode_adapter

_RECORD = dict(
    session_id=7,
    sync_private_key="sync-private",
    host_public_key="ssh-ed25519 AAAAhost",
    provider_instance_id="555",
    launch_attempt_id="attempt-1",
)


# -- GCE -------------------------------------------------------------------------------------------


def _gce_instance(status="RUNNING", ip="34.1.2.3", session="7", attempt="attempt-1", instance_id=555):
    access = [SimpleNamespace(nat_i_p=ip)] if ip else []
    return SimpleNamespace(
        id=instance_id,
        status=status,
        labels={"bioaf-session": session, "bioaf-launch": attempt},
        network_interfaces=[SimpleNamespace(access_configs=access, network_i_p="10.128.0.5")],
    )


async def _gce_report(get, read=None, **over):
    provider = build_mode_adapter(GCEWorkNodeProvider, mode=ComputeMode.KUBERNETES)
    provider._gcp_config = {"gcp_project_id": "proj"}
    client = MagicMock()
    if isinstance(get, Exception):
        client.get.side_effect = get
    else:
        client.get.return_value = get
    read = read or AsyncMock(return_value=NodeReport(ssh_policy_status="applied"))
    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch("app.adapters.work_nodes.gce.read_node_report", read),
    ):
        result = await provider.report_vm("bioaf-worknode-7", "us-central1-b", **{**_RECORD, **over})
    return result, read, client


@pytest.mark.asyncio
async def test_gce_reads_the_report_over_the_recorded_public_route():
    result, read, _ = await _gce_report(_gce_instance(), sync_route="public")
    assert (result.outcome, result.ssh_policy_status) == ("reported", "applied")
    assert read.call_args.args[0] == "34.1.2.3"
    assert read.call_args.kwargs["host_public_key"] == "ssh-ed25519 AAAAhost"
    assert read.call_args.kwargs["sync_private_key"] == "sync-private"


@pytest.mark.asyncio
async def test_gce_private_route_uses_the_internal_address_only():
    result, read, _ = await _gce_report(_gce_instance(), sync_route="private-v1")
    assert result.outcome == "reported"
    assert [c.args[0] for c in read.call_args_list] == ["10.128.0.5"]


@pytest.mark.asyncio
async def test_gce_private_route_failure_never_falls_back_to_public():
    result, read, _ = await _gce_report(
        _gce_instance(), read=AsyncMock(side_effect=NodeSyncError("TimeoutError")), sync_route="private-v1"
    )
    assert result.outcome == "unreachable"
    assert [c.args[0] for c in read.call_args_list] == ["10.128.0.5"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instance",
    [
        _gce_instance(instance_id=999),  # replaced VM under the same name
        _gce_instance(session="8"),
        _gce_instance(attempt="attempt-2"),
    ],
)
async def test_gce_a_different_instance_is_a_mismatch_and_is_not_contacted(instance):
    result, read, _ = await _gce_report(instance, sync_route="public")
    assert result.outcome == "mismatch"
    read.assert_not_called()


@pytest.mark.asyncio
async def test_gce_a_missing_vm_is_a_mismatch():
    from google.api_core.exceptions import NotFound

    result, read, _ = await _gce_report(NotFound("gone"), sync_route="public")
    assert result.outcome == "mismatch"
    read.assert_not_called()


@pytest.mark.asyncio
async def test_gce_an_unreadable_provider_is_unavailable():
    result, read, _ = await _gce_report(RuntimeError("quota"), sync_route="public")
    assert result.outcome == "unavailable"
    read.assert_not_called()


@pytest.mark.asyncio
async def test_gce_a_vm_that_is_not_running_is_not_contacted():
    result, read, _ = await _gce_report(_gce_instance(status="STOPPING"), sync_route="public")
    assert result.outcome == "unreachable"
    read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,outcome",
    [
        (NodeTrustError("unrecognized server key"), "untrusted"),
        (NodeReportMalformed("bad"), "malformed"),
        (NodeSyncError("ConnectionRefusedError"), "unreachable"),
    ],
)
async def test_gce_ssh_outcomes_are_classified(error, outcome):
    result, _, _ = await _gce_report(_gce_instance(), read=AsyncMock(side_effect=error), sync_route="public")
    assert result.outcome == outcome
    assert result.ssh_policy_status is None


@pytest.mark.asyncio
async def test_gce_no_address_is_unreachable_without_connecting():
    result, read, _ = await _gce_report(_gce_instance(ip=None), sync_route="public")
    assert result.outcome == "unreachable"
    read.assert_not_called()


@pytest.mark.asyncio
async def test_gce_the_provider_read_does_not_hold_the_event_loop():
    import threading

    seen = {}

    def get(**kwargs):
        seen["thread"] = threading.current_thread()
        return _gce_instance()

    provider = build_mode_adapter(GCEWorkNodeProvider, mode=ComputeMode.KUBERNETES)
    provider._gcp_config = {"gcp_project_id": "proj"}
    client = MagicMock()
    client.get.side_effect = get
    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch("app.adapters.work_nodes.gce.read_node_report", AsyncMock(return_value=NodeReport(None))),
    ):
        await provider.report_vm("bioaf-worknode-7", "us-central1-b", sync_route="public", **_RECORD)
    assert seen["thread"] is not threading.main_thread()


@pytest.mark.asyncio
async def test_simulated_mode_reports_unavailable_rather_than_inventing_liveness():
    provider = build_mode_adapter(GCEWorkNodeProvider, mode=ComputeMode.LOCAL)
    result = await provider.report_vm("bioaf-worknode-7", "us-central1-b", sync_route="public", **_RECORD)
    assert result.outcome == "unavailable"


# -- EC2 -------------------------------------------------------------------------------------------


def _ec2_instance(state="running", public="1.2.3.4", private="10.20.0.5", session="7", attempt="attempt-1"):
    tags = [{"Key": "bioaf-session", "Value": session}, {"Key": "bioaf-launch", "Value": attempt}]
    inst = {"InstanceId": "i-0abc", "State": {"Name": state}, "Tags": tags, "PrivateIpAddress": private}
    if public:
        inst["PublicIpAddress"] = public
    return {"Reservations": [{"Instances": [inst]}]}


async def _ec2_report(describe, read=None, **over):
    provider = build_mode_adapter(Ec2WorkNodeProvider, mode=ComputeMode.KUBERNETES)
    provider._aws_config = {"aws_region": "us-west-1"}
    ec2 = MagicMock()
    if isinstance(describe, Exception):
        ec2.describe_instances.side_effect = describe
    else:
        ec2.describe_instances.return_value = describe
    read = read or AsyncMock(return_value=NodeReport(ssh_policy_status="failed_closed"))
    with (
        patch.object(provider, "_client", return_value=ec2),
        patch("app.adapters.work_nodes.ec2.read_node_report", read),
    ):
        result = await provider.report_vm("i-0abc", "us-west-1a", **{**_RECORD, **over})
    return result, read


@pytest.mark.asyncio
async def test_ec2_reads_the_report_over_the_recorded_public_route():
    result, read = await _ec2_report(_ec2_instance(), sync_route="public")
    assert (result.outcome, result.ssh_policy_status) == ("reported", "failed_closed")
    assert read.call_args.args[0] == "1.2.3.4"


@pytest.mark.asyncio
async def test_ec2_a_route_recorded_before_routes_existed_keeps_the_public_address():
    result, read = await _ec2_report(_ec2_instance(), sync_route=None)
    assert read.call_args.args[0] == "1.2.3.4"


@pytest.mark.asyncio
async def test_ec2_private_route_uses_the_peered_address_and_never_the_public_one():
    result, read = await _ec2_report(
        _ec2_instance(), read=AsyncMock(side_effect=NodeSyncError("TimeoutError")), sync_route="private-v1"
    )
    assert result.outcome == "unreachable"
    assert [c.args[0] for c in read.call_args_list] == ["10.20.0.5"]


@pytest.mark.asyncio
@pytest.mark.parametrize("instance", [_ec2_instance(session="8"), _ec2_instance(attempt="attempt-2")])
async def test_ec2_a_different_instance_is_a_mismatch(instance):
    result, read = await _ec2_report(instance, sync_route="public")
    assert result.outcome == "mismatch"
    read.assert_not_called()


@pytest.mark.asyncio
async def test_ec2_an_unreadable_provider_is_unavailable():
    result, read = await _ec2_report(RuntimeError("throttled"), sync_route="public")
    assert result.outcome == "unavailable"
    read.assert_not_called()


@pytest.mark.asyncio
async def test_ec2_a_stopped_instance_is_not_contacted():
    result, read = await _ec2_report(_ec2_instance(state="stopped"), sync_route="public")
    assert result.outcome == "unreachable"
    read.assert_not_called()
