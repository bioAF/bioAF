"""Plan 03.5A: the notebook gateway's port (8443) opens wherever the application's HTTPS (443) is open.

Existing installations update without a manual firewall step: the backend reconciles it with the
same provider authority it uses for the host's other network rules. The gateway gets exactly the
sources that reach 443 on this host (public installations: everyone; private ones: their own
ranges), never wider, and a rule that already covers 8443 is left alone.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.adapters.host_network.firewall import GATEWAY_PORT, GATEWAY_RULE, AwsHostNetwork, GcpHostNetwork
from app.services import notebook_gateway_network
from app.services.ssh_recovery import HostIdentity, RecoveryError
from tests.test_host_ssh_filter import GCP, FakeFirewalls, _gcp_allow, _rule_summary


def _web(name="bioaf-allow-web", *, ports=("80", "443"), sources=("0.0.0.0/0",), tags=("bioaf",), **kw):
    return _gcp_allow(name, priority=1000, ports=ports, sources=sources, tags=tags, **kw)


# -- GCP -------------------------------------------------------------------------------------------


def test_gcp_opens_the_gateway_port_to_the_sources_that_reach_https():
    fw = FakeFirewalls([_web(), _web("lab-https", ports=("443",), sources=("10.20.0.0/16",))])
    result = GcpHostNetwork(GCP, client=fw, target_tag="bioaf").ensure_gateway_port()
    assert result == {"opened": True, "sources": ["0.0.0.0/0", "10.20.0.0/16"]}
    assert _rule_summary(fw.rules[GATEWAY_RULE]) == {
        "priority": 1000,
        "sources": ["0.0.0.0/0", "10.20.0.0/16"],
        "targets": ["bioaf"],
        "allowed": [("tcp", [str(GATEWAY_PORT)])],
        "denied": [],
        "network": "projects/lab-project/global/networks/default",
    }


def test_gcp_a_private_installation_gets_only_its_own_ranges():
    fw = FakeFirewalls([_web(sources=("192.168.10.0/24",))])
    GcpHostNetwork(GCP, client=fw, target_tag="bioaf").ensure_gateway_port()
    assert list(fw.rules[GATEWAY_RULE].source_ranges) == ["192.168.10.0/24"]


def test_gcp_an_installation_whose_web_rule_already_covers_the_gateway_is_left_alone():
    fw = FakeFirewalls([_web(ports=("80", "443", "8443"))])
    result = GcpHostNetwork(GCP, client=fw, target_tag="bioaf").ensure_gateway_port()
    assert result == {"opened": False, "sources": []}
    assert GATEWAY_RULE not in fw.rules
    assert fw.calls == []


@pytest.mark.parametrize(
    "rule",
    [
        _web(tags=("other-host",)),  # another host's rule
        _web(disabled=True),
        _web(ports=("80",)),  # not HTTPS
    ],
)
def test_gcp_rules_that_do_not_open_https_on_this_host_do_not_open_the_gateway(rule):
    fw = FakeFirewalls([rule])
    result = GcpHostNetwork(GCP, client=fw, target_tag="bioaf").ensure_gateway_port()
    assert result == {"opened": False, "sources": []}
    assert GATEWAY_RULE not in fw.rules


def test_gcp_the_gateway_rule_follows_a_narrowed_https_rule():
    fw = FakeFirewalls([_web()])
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_gateway_port()
    fw.rules["bioaf-allow-web"] = _web(sources=("203.0.113.0/24",))
    net.ensure_gateway_port()
    assert list(fw.rules[GATEWAY_RULE].source_ranges) == ["203.0.113.0/24"]


# -- AWS -------------------------------------------------------------------------------------------

AWS = HostIdentity(
    provider="aws",
    account="043671579834",
    region="us-west-1",
    zone="us-west-1a",
    instance_id="i-0abc",
    instance_name="bioaf",
    public=True,
    network="vpc-1",
)


def _ec2(permissions):
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"InstanceId": "i-0abc", "VpcId": "vpc-1", "SecurityGroups": [{"GroupId": "sg-web"}]}]}
        ]
    }
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-web", "IpPermissions": permissions}]
    }
    return ec2


def _perm(from_port, to_port, **sources):
    return {"IpProtocol": "tcp", "FromPort": from_port, "ToPort": to_port, **sources}


def test_aws_adds_the_gateway_port_beside_each_https_permission_with_the_same_sources():
    ec2 = _ec2(
        [
            _perm(443, 443, IpRanges=[{"CidrIp": "0.0.0.0/0"}], Ipv6Ranges=[{"CidrIpv6": "::/0"}]),
            _perm(80, 80, IpRanges=[{"CidrIp": "0.0.0.0/0"}]),
            _perm(443, 443, PrefixListIds=[{"PrefixListId": "pl-lab"}]),
        ]
    )
    result = AwsHostNetwork(AWS, client=ec2).ensure_gateway_port()
    assert result["opened"] is True
    calls = [c.kwargs for c in ec2.authorize_security_group_ingress.call_args_list]
    assert calls == [
        {
            "GroupId": "sg-web",
            "IpPermissions": [_perm(8443, 8443, IpRanges=[{"CidrIp": "0.0.0.0/0"}], Ipv6Ranges=[{"CidrIpv6": "::/0"}])],
        },
        {"GroupId": "sg-web", "IpPermissions": [_perm(8443, 8443, PrefixListIds=[{"PrefixListId": "pl-lab"}])]},
    ]


def test_aws_a_group_that_already_reaches_the_gateway_port_is_left_alone():
    ec2 = _ec2(
        [
            _perm(443, 443, IpRanges=[{"CidrIp": "0.0.0.0/0"}]),
            _perm(8443, 8443, IpRanges=[{"CidrIp": "0.0.0.0/0"}]),
        ]
    )
    assert AwsHostNetwork(AWS, client=ec2).ensure_gateway_port() == {"opened": False, "sources": []}
    ec2.authorize_security_group_ingress.assert_not_called()


def test_aws_an_existing_identical_permission_is_not_an_error():
    ec2 = _ec2([_perm(443, 443, IpRanges=[{"CidrIp": "0.0.0.0/0"}])])
    ec2.authorize_security_group_ingress.side_effect = RuntimeError("InvalidPermission.Duplicate")
    assert AwsHostNetwork(AWS, client=ec2).ensure_gateway_port()["opened"] is True


# -- The reconciliation the backend runs -------------------------------------------------------------


async def _recorded(session):
    import json

    raw = (
        await session.execute(
            text("SELECT value FROM platform_config WHERE key = :k"), {"k": notebook_gateway_network.STATUS_KEY}
        )
    ).scalar()
    return json.loads(raw) if raw else None


@pytest.mark.asyncio
async def test_the_backend_reconciles_the_port_and_records_the_result(session):
    driver = MagicMock()
    driver.ensure_gateway_port.return_value = {"opened": True, "sources": ["0.0.0.0/0"]}
    with (
        patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=GCP)),
        patch("app.services.host_ssh_access.network_for", AsyncMock(return_value=driver)),
    ):
        await notebook_gateway_network.ensure_gateway_port(session)
    await session.commit()
    recorded = await _recorded(session)
    assert recorded["status"] == "ok" and recorded["opened"] is True and recorded["sources"] == ["0.0.0.0/0"]


@pytest.mark.asyncio
async def test_a_provider_failure_is_recorded_and_never_raised(session):
    driver = MagicMock()
    driver.ensure_gateway_port.side_effect = PermissionError("compute.firewalls.create denied")
    with (
        patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=GCP)),
        patch("app.services.host_ssh_access.network_for", AsyncMock(return_value=driver)),
    ):
        await notebook_gateway_network.ensure_gateway_port(session)
    await session.commit()
    recorded = await _recorded(session)
    assert recorded["status"] == "failed"
    assert "PermissionError" in recorded["detail"]


@pytest.mark.asyncio
async def test_a_server_that_is_not_a_cloud_host_is_skipped(session):
    with patch("app.services.ssh_recovery.current_host_identity", AsyncMock(side_effect=RecoveryError("local"))):
        await notebook_gateway_network.ensure_gateway_port(session)
    await session.commit()
    assert (await _recorded(session))["status"] == "not_applicable"


def test_the_reconciliation_is_a_periodic_job_that_runs_soon_after_start():
    from app.tasks import jobs

    job = next(j for j in jobs.PERIODIC_JOBS if j.role == "notebook-gateway-port")
    assert job.first_delay <= jobs.MINUTE
    assert job.interval == 6 * jobs.HOUR
