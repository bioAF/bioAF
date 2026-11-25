"""Plan 03B: backend sync reaches new Work Nodes over the private network.

GCP: a backend-managed rule allows tcp:22 only from the application VM's internal /32 to the
backend-sync tag at priority 800. AWS: the compute module's peering and backend-sync security group
(once deployed). A failed private route never silently falls back to public sync.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ssh_recovery import HostIdentity

GCP = HostIdentity(
    provider="gcp",
    account="lab-project",
    region="us-central1",
    zone="us-central1-b",
    instance_id="481516",
    instance_name="bioaf",
    public=True,
    network="default",
    internal_ip="10.128.0.2",
)


class Firewalls:
    def __init__(self):
        self.rules = {}

    def get(self, project, firewall):
        from google.api_core.exceptions import NotFound

        if firewall not in self.rules:
            raise NotFound("x")
        return self.rules[firewall]

    def insert(self, project, firewall_resource):
        self.rules[firewall_resource.name] = firewall_resource
        return SimpleNamespace(result=lambda timeout=None: None)

    def patch(self, project, firewall, firewall_resource):
        self.rules[firewall] = firewall_resource
        return SimpleNamespace(result=lambda timeout=None: None)


@pytest.mark.asyncio
async def test_gcp_private_route_allows_only_the_app_internal_address_to_the_sync_tag(session):
    from app.services.backend_sync_route import SYNC_TAG, ensure_backend_sync_route

    fw = Firewalls()
    with (
        patch("app.services.backend_sync_route._simulated", return_value=False),
        patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=GCP)),
        patch("app.services.backend_sync_route._gcp_firewalls", AsyncMock(return_value=fw)),
    ):
        route = await ensure_backend_sync_route(session)
    assert route == {"route": "private-v1", "network": "default", "tag": SYNC_TAG}
    rule = fw.rules["bioaf-work-node-sync-from-app"]
    assert list(rule.source_ranges) == ["10.128.0.2/32"]
    assert list(rule.target_tags) == [SYNC_TAG]
    assert rule.priority == 800
    assert [(a.I_p_protocol, list(a.ports)) for a in rule.allowed] == [("tcp", ["22"])]
    assert not rule.source_tags and not rule.source_service_accounts


@pytest.mark.asyncio
async def test_a_replacement_app_vm_moves_the_rule_to_its_new_address(session):
    from dataclasses import replace

    from app.services.backend_sync_route import ensure_backend_sync_route

    fw = Firewalls()
    with (
        patch("app.services.backend_sync_route._simulated", return_value=False),
        patch("app.services.backend_sync_route._gcp_firewalls", AsyncMock(return_value=fw)),
    ):
        with patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=GCP)):
            await ensure_backend_sync_route(session)
        with patch(
            "app.services.ssh_recovery.current_host_identity",
            AsyncMock(return_value=replace(GCP, internal_ip="10.128.0.9")),
        ):
            await ensure_backend_sync_route(session)
    assert list(fw.rules["bioaf-work-node-sync-from-app"].source_ranges) == ["10.128.0.9/32"]


@pytest.mark.asyncio
async def test_a_failed_private_route_stops_the_launch_instead_of_using_public_sync(session):
    from app.exceptions import ValidationError
    from app.services.backend_sync_route import ensure_backend_sync_route

    broken = MagicMock()
    broken.get.side_effect = RuntimeError("permission denied")
    with (
        patch("app.services.backend_sync_route._simulated", return_value=False),
        patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=GCP)),
        patch("app.services.backend_sync_route._gcp_firewalls", AsyncMock(return_value=broken)),
        pytest.raises(ValidationError, match="private connection"),
    ):
        await ensure_backend_sync_route(session)


@pytest.mark.asyncio
async def test_aws_uses_the_deployed_backend_sync_group_or_the_existing_public_route(session):
    from app.platform.platform_config_service import PlatformConfigService
    from app.services.backend_sync_route import ensure_backend_sync_route

    aws = HostIdentity(
        provider="aws",
        account="123456789012",
        region="us-west-1",
        zone="us-west-1b",
        instance_id="i-0abc123def4567890",
        instance_name="bioaf",
        public=True,
    )
    with (
        patch("app.services.backend_sync_route._simulated", return_value=False),
        patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=aws)),
    ):
        assert (await ensure_backend_sync_route(session))["route"] == "public"
        await PlatformConfigService.set(session, "aws_backend_sync_security_group_id", "sg-0sync")
        assert await ensure_backend_sync_route(session) == {"route": "private-v1", "security_group": "sg-0sync"}


@pytest.mark.asyncio
async def test_simulated_installations_keep_the_public_route(session):
    from app.services.backend_sync_route import ensure_backend_sync_route

    with patch("app.services.backend_sync_route._simulated", return_value=True):
        assert (await ensure_backend_sync_route(session))["route"] == "public"


# -- Adapters use the private address for private-route nodes ---------------------------------


@pytest.mark.asyncio
async def test_gce_private_route_nodes_get_the_sync_tag_and_sync_over_the_internal_address():
    from tests.test_gce_adapter import _gce_delete, _gce_instance, _launch_provider, _launch_vm_spec, _not_found

    provider = _launch_provider()
    client = MagicMock()
    client.insert.return_value.result.return_value = None
    with (
        patch("google.cloud.compute_v1.InstancesClient", return_value=client),
        patch.object(provider, "_get_gcp_credentials", return_value=MagicMock()),
        patch.object(provider, "_poll_vm_ready", new=MagicMock()),
        patch("app.adapters.work_nodes.gce.runtime.spawn"),
    ):
        await provider._gce_launch_vm(
            {**_launch_vm_spec(), "sync_route": "private-v1", "network": "lab-net", "sync_tag": "bioaf-backend-sync"}
        )
    instance = client.insert.call_args.kwargs["instance_resource"]
    assert "bioaf-backend-sync" in instance.tags.items
    assert instance.network_interfaces[0].network == "global/networks/lab-net"

    delete_client = MagicMock()
    delete_client.get.side_effect = [_gce_instance(), _not_found()]
    result, sync = await _gce_delete(delete_client, sync_route="private-v1")
    assert sync.call_args.args[0] == "10.128.0.5"
    assert result.stop_failure is None


@pytest.mark.asyncio
async def test_ec2_private_route_nodes_join_the_sync_group_and_sync_over_the_private_address():
    from tests.test_work_node_ec2 import _aws_provider, _delete, _instance, _vm_spec

    provider = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_images.return_value = {"Images": [{"ImageId": "ami-1", "CreationDate": "2026"}]}
    ec2.run_instances.return_value = {"OwnerId": "1", "Instances": [{"InstanceId": "i-0new", "Placement": {}}]}
    with (
        patch.object(provider, "_client", return_value=ec2),
        patch.object(provider, "_poll_vm_ready", new=MagicMock()),
        patch("app.adapters.work_nodes.ec2.runtime.spawn"),
    ):
        await provider.provision(_vm_spec(sync_route="private-v1", backend_sync_security_group="sg-0sync"))
    groups = ec2.run_instances.call_args.kwargs["NetworkInterfaces"][0]["Groups"]
    assert groups == ["sg-xyz", "sg-0sync"]

    p = _aws_provider()
    ec2 = MagicMock()
    inst = _instance()
    inst["Reservations"][0]["Instances"][0]["PrivateIpAddress"] = "10.0.1.7"
    term = _instance(state="terminated")
    ec2.describe_instances.side_effect = [inst, term]
    sync = AsyncMock(return_value=0)
    await _delete(p, ec2, sync, sync_route="private-v1")
    assert sync.call_args.args[0] == "10.0.1.7"


from tests import test_work_nodes as _work_node_suite  # noqa: E402

seed_environment = _work_node_suite.seed_environment
seed_platform_config = _work_node_suite.seed_platform_config
seed_project = _work_node_suite.seed_project
seed_session_credentials = _work_node_suite.seed_session_credentials


@pytest.mark.asyncio
async def test_a_launch_whose_private_route_fails_is_refused_before_anything_is_recorded(
    client,
    session,
    comp_bio_token,
    comp_bio_user,
    seed_environment,
    seed_project,
    seed_session_credentials,
    seed_platform_config,
):
    from sqlalchemy import func, select

    from app.exceptions import ValidationError
    from app.models.notebook_session import ComputeSession
    from app.services.backend_sync_route import ROUTE_FAILED

    adapter = MagicMock()
    adapter.launch_vm = AsyncMock()
    with (
        patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter),
        patch(
            "app.services.backend_sync_route.ensure_backend_sync_route",
            AsyncMock(side_effect=ValidationError(ROUTE_FAILED)),
        ),
    ):
        response = await client.post(
            "/api/v1/work-nodes/sessions",
            json={
                "project_id": seed_project.id,
                "environment_version_id": seed_environment["ready_version"].id,
                "machine_type": "n2-standard-4",
            },
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
    assert response.status_code == 400
    assert response.json()["detail"] == ROUTE_FAILED
    assert (await session.execute(select(func.count()).select_from(ComputeSession))).scalar_one() == 0
    adapter.launch_vm.assert_not_called()
