"""Plan 03A first-update gate: is it safe to replace a backend that may hold legacy sync keys in memory?

Work Nodes launched before durable credentials keep their sync key only in the old backend
process. Replacing that process while such a node may still exist loses the key. The check reads
the database without assuming the new columns exist and reconciles it with an authenticated
provider inventory; an unavailable inventory is never treated as "nothing running".
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.notebook_session import ComputeSession
from app.services.work_node_readiness import (
    InventoryUnavailable,
    ManagedInstance,
    assess_update_readiness,
)


def _row(user, status, *, instance=None, durable=False, **extra) -> ComputeSession:
    return ComputeSession(
        user_id=user.id,
        organization_id=user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        cpu_cores=2,
        memory_gb=4,
        status=status,
        gce_instance_name=instance,
        node_trust_version=1 if durable else None,
        sync_private_key="k" if durable else None,
        **extra,
    )


async def _seed(session, *rows):
    session.add_all(rows)
    await session.commit()
    return rows


def _inventory(*items):
    return AsyncMock(return_value=[ManagedInstance(name=n, session_id=s) for n, s in items])


@pytest.mark.asyncio
async def test_no_work_node_ever_launched_is_ready_without_asking_the_provider(session):
    inventory = _inventory()
    result = await assess_update_readiness(session, inventory)
    assert result.ready
    inventory.assert_not_called()


@pytest.mark.asyncio
async def test_a_live_legacy_node_defers_the_update(session, admin_user):
    (row,) = await _seed(session, _row(admin_user, "running", instance="bioaf-worknode-1"))
    result = await assess_update_readiness(session, _inventory(("bioaf-worknode-1", str(row.id))))
    assert not result.ready
    assert result.unsafe_count == 1


@pytest.mark.asyncio
async def test_a_misleading_stopped_record_with_a_live_instance_defers(session, admin_user):
    (row,) = await _seed(session, _row(admin_user, "stopped", instance="bioaf-worknode-2"))
    result = await assess_update_readiness(session, _inventory(("bioaf-worknode-2", str(row.id))))
    assert not result.ready


@pytest.mark.asyncio
async def test_a_failed_record_with_a_retained_instance_defers(session, admin_user):
    (row,) = await _seed(session, _row(admin_user, "failed", instance="bioaf-worknode-3"))
    result = await assess_update_readiness(session, _inventory(("bioaf-worknode-3", str(row.id))))
    assert not result.ready


@pytest.mark.asyncio
async def test_an_in_flight_legacy_launch_defers(session, admin_user):
    await _seed(session, _row(admin_user, "starting"))
    result = await assess_update_readiness(session, _inventory())
    assert not result.ready


@pytest.mark.asyncio
async def test_confirmed_deleted_legacy_records_do_not_block_forever(session, admin_user):
    await _seed(
        session,
        _row(admin_user, "running", instance="bioaf-worknode-gone"),
        _row(admin_user, "stopped", instance="bioaf-worknode-old"),
    )
    result = await assess_update_readiness(session, _inventory())
    assert result.ready


@pytest.mark.asyncio
async def test_durable_nodes_may_keep_running_across_the_restart(session, admin_user):
    (row,) = await _seed(session, _row(admin_user, "running", instance="bioaf-worknode-9", durable=True))
    result = await assess_update_readiness(session, _inventory(("bioaf-worknode-9", str(row.id))))
    assert result.ready


@pytest.mark.asyncio
async def test_a_live_instance_with_no_record_defers(session, admin_user):
    await _seed(session, _row(admin_user, "stopped", instance="bioaf-worknode-old"))
    result = await assess_update_readiness(session, _inventory(("bioaf-worknode-777", "777")))
    assert not result.ready


@pytest.mark.asyncio
async def test_an_unavailable_inventory_is_not_treated_as_empty(session, admin_user):
    await _seed(session, _row(admin_user, "stopped", instance="bioaf-worknode-old"))
    inventory = AsyncMock(side_effect=InventoryUnavailable("denied"))
    with pytest.raises(InventoryUnavailable):
        await assess_update_readiness(session, inventory)


@pytest.mark.asyncio
async def test_an_installed_schema_without_trust_columns_treats_every_node_as_legacy(session, admin_user):
    (row,) = await _seed(session, _row(admin_user, "running", instance="bioaf-worknode-5", durable=True))
    with patch("app.services.work_node_readiness._has_trust_column", AsyncMock(return_value=False)):
        result = await assess_update_readiness(session, _inventory(("bioaf-worknode-5", str(row.id))))
    assert not result.ready


# -- Provider inventories fail closed --------------------------------------------------------


@pytest.mark.asyncio
async def test_gce_inventory_covers_every_zone_and_raises_on_failure():
    from tests.support.mode_adapters import build_mode_adapter
    from app.adapters.work_nodes.gce import GCEWorkNodeProvider
    from app.settings_contract import ComputeMode
    from types import SimpleNamespace

    provider = build_mode_adapter(GCEWorkNodeProvider, mode=ComputeMode.KUBERNETES)
    provider._gcp_config = {"gcp_project_id": "proj", "gcp_zone": "us-central1-b"}
    client = MagicMock()
    client.aggregated_list.return_value = [
        ("zones/us-central1-b", SimpleNamespace(instances=[SimpleNamespace(name="a", labels={"bioaf-session": "1"})])),
        ("zones/us-central1-f", SimpleNamespace(instances=[SimpleNamespace(name="b", labels={"bioaf-session": "2"})])),
        ("zones/us-east1-b", SimpleNamespace(instances=[])),
    ]
    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
    ):
        found = await provider.inventory_instances()
        assert {(i.name, i.session_id) for i in found} == {("a", "1"), ("b", "2")}
        client.aggregated_list.side_effect = RuntimeError("permission denied")
        with pytest.raises(InventoryUnavailable):
            await provider.inventory_instances()


@pytest.mark.asyncio
async def test_ec2_inventory_excludes_terminated_and_raises_on_failure():
    from tests.support.mode_adapters import build_mode_adapter
    from app.adapters.work_nodes.ec2 import Ec2VmInstance
    from app.settings_contract import ComputeMode

    provider = build_mode_adapter(Ec2VmInstance, mode=ComputeMode.KUBERNETES)
    provider._aws_config = {"aws_region": "us-west-1"}
    ec2 = MagicMock()
    ec2.get_paginator.return_value.paginate.return_value = [
        {
            "Reservations": [
                {"Instances": [{"InstanceId": "i-1", "Tags": [{"Key": "bioaf-session", "Value": "4"}]}]},
            ]
        }
    ]
    with patch.object(provider, "_client", return_value=ec2):
        found = await provider.inventory_instances()
        assert [(i.name, i.session_id) for i in found] == [("i-1", "4")]
        filters = ec2.get_paginator.return_value.paginate.call_args.kwargs["Filters"]
        states = next(f["Values"] for f in filters if f["Name"] == "instance-state-name")
        assert "terminated" not in states and "stopped" in states
        ec2.get_paginator.return_value.paginate.side_effect = RuntimeError("AccessDenied")
        with pytest.raises(InventoryUnavailable):
            await provider.inventory_instances()


# -- The command the updater runs ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome, expected",
    [("ready", 0), ("deferred", 3), ("unavailable", 1)],
)
async def test_check_command_exit_status(outcome, expected, capsys):
    from app.cli import check_work_node_readiness as cli
    from app.services.work_node_readiness import Readiness

    async def fake_check():
        if outcome == "unavailable":
            raise InventoryUnavailable("x")
        return Readiness(ready=outcome == "ready", unsafe_count=0 if outcome == "ready" else 2)

    with patch.object(cli, "_check", fake_check):
        assert await cli.run() == expected
