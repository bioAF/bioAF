"""Plan 03.5A: the notebook gateway's port (8443) opens wherever the application's HTTPS (443) is open.

Existing installations update without a manual firewall step: the backend reconciles it with the
same provider authority it uses for the host's other network rules. The gateway is reachable from
exactly the sources that reach 443 on this host (public installations: everyone; private ones: their
own ranges), never wider: on GCP each HTTPS allow and deny is mirrored onto tcp:8443 at its own
priority, on AWS each HTTPS permission's sources are added for 8443. Access narrows and closes with
HTTPS. bioAF changes only the 8443 access it created: HTTPS rules and the operator's own 8443 rules
are never modified, so the web portal's access on 443 is unchanged.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.adapters.host_network.firewall import (
    AWS_GATEWAY_MARK,
    GATEWAY_PORT,
    GATEWAY_RULE,
    AwsHostNetwork,
    GcpHostNetwork,
    HostTargets,
    gateway_rule_name,
)
from app.services import notebook_gateway_network
from app.services.ssh_recovery import HostIdentity, RecoveryError
from tests.test_host_ssh_filter import GCP, FakeFirewalls, _gcp_allow, _rule_summary


def _web(name="bioaf-allow-web", *, ports=("80", "443"), sources=("0.0.0.0/0",), tags=("bioaf",), **kw):
    return _gcp_allow(name, priority=kw.pop("priority", 1000), ports=ports, sources=sources, tags=tags, **kw)


def _deny(name, *, priority, sources, ports=("443",), tags=("bioaf",), target_sas=()):
    from google.cloud import compute_v1

    return compute_v1.Firewall(
        name=name,
        priority=priority,
        direction="INGRESS",
        network="projects/lab-project/global/networks/default",
        source_ranges=list(sources),
        target_tags=list(tags),
        target_service_accounts=list(target_sas),
        denied=[compute_v1.Denied(I_p_protocol="tcp", ports=list(ports))],
    )


def _for_sa(rule, *accounts):
    rule.target_tags = []
    rule.target_service_accounts = list(accounts)
    return rule


def _mirrors(fw) -> dict:
    """bioAF's gateway rules, keyed by the HTTPS rule each mirrors."""
    return {n: r for n, r in fw.rules.items() if n == GATEWAY_RULE or n.startswith(GATEWAY_RULE + "-")}


def _effects(fw) -> dict:
    """Every rule that is not bioAF's gateway rule, as comparable data."""
    return {n: type(r).to_json(r) for n, r in fw.rules.items() if n not in _mirrors(fw)}


def _ensure(fw, targets=None):
    return GcpHostNetwork(GCP, client=fw, target_tag="bioaf").ensure_gateway_port(targets)


# -- GCP -------------------------------------------------------------------------------------------


def test_gcp_mirrors_each_https_rule_onto_the_gateway_port_at_its_own_priority():
    fw = FakeFirewalls([_web(), _web("lab-https", ports=("443",), sources=("10.20.0.0/16",), priority=900)])
    result = _ensure(fw)
    assert result["opened"] is True and result["sources"] == ["0.0.0.0/0", "10.20.0.0/16"]
    assert _rule_summary(fw.rules[gateway_rule_name("bioaf-allow-web")]) == {
        "priority": 1000,
        "sources": ["0.0.0.0/0"],
        "targets": ["bioaf"],
        "allowed": [("tcp", [str(GATEWAY_PORT)])],
        "denied": [],
        "network": "projects/lab-project/global/networks/default",
    }
    lab = fw.rules[gateway_rule_name("lab-https")]
    assert (lab.priority, list(lab.source_ranges)) == (900, ["10.20.0.0/16"])
    assert set(_mirrors(fw)) == {gateway_rule_name("bioaf-allow-web"), gateway_rule_name("lab-https")}


def test_gcp_a_private_installation_gets_only_its_own_ranges():
    fw = FakeFirewalls([_web(sources=("192.168.10.0/24",))])
    _ensure(fw)
    assert [list(r.source_ranges) for r in _mirrors(fw).values()] == [["192.168.10.0/24"]]


def test_gcp_an_installation_whose_web_rule_already_covers_the_gateway_is_left_alone():
    fw = FakeFirewalls([_web(ports=("80", "443", "8443"))])
    result = _ensure(fw)
    assert result["opened"] is False and result["sources"] == []
    assert _mirrors(fw) == {}
    assert fw.calls == []


@pytest.mark.parametrize(
    "rule",
    [
        _web(tags=("other-host",)),  # another host's rule
        _web(disabled=True),
        _web(ports=("80",)),  # not HTTPS
        _for_sa(_web(), "other@lab-project.iam.gserviceaccount.com"),  # may apply to another instance
    ],
)
def test_gcp_rules_that_do_not_open_https_on_this_host_do_not_open_the_gateway(rule):
    fw = FakeFirewalls([rule])
    result = _ensure(fw)
    assert result["opened"] is False and result["sources"] == []
    assert _mirrors(fw) == {}


def test_gcp_the_gateway_follows_a_narrowed_https_rule():
    fw = FakeFirewalls([_web()])
    _ensure(fw)
    fw.rules["bioaf-allow-web"] = _web(sources=("203.0.113.0/24",))
    _ensure(fw)
    assert [list(r.source_ranges) for r in _mirrors(fw).values()] == [["203.0.113.0/24"]]


def test_gcp_the_gateway_closes_when_https_is_closed():
    fw = FakeFirewalls([_web()])
    _ensure(fw)
    del fw.rules["bioaf-allow-web"]
    assert _ensure(fw) == {"opened": False, "sources": []}
    assert _mirrors(fw) == {}


def test_gcp_a_removed_https_rule_takes_only_its_own_mirror_with_it():
    fw = FakeFirewalls([_web(), _web("lab-https", ports=("443",), sources=("10.20.0.0/16",))])
    _ensure(fw)
    del fw.rules["bioaf-allow-web"]
    _ensure(fw)
    assert set(_mirrors(fw)) == {gateway_rule_name("lab-https")}


def test_gcp_a_https_exception_is_kept_on_the_gateway():
    """HTTPS for everyone except a blocked address: the blocked address cannot reach notebooks either."""
    fw = FakeFirewalls([_web(), _deny("block-one", priority=900, sources=("198.51.100.7/32",))])
    _ensure(fw)
    deny = fw.rules[gateway_rule_name("block-one")]
    assert _rule_summary(deny) == {
        "priority": 900,
        "sources": ["198.51.100.7/32"],
        "targets": ["bioaf"],
        "allowed": [],
        "denied": [("tcp", [str(GATEWAY_PORT)])],
        "network": "projects/lab-project/global/networks/default",
    }
    # Restrictions take effect before the access they restrict.
    inserts = [name for op, name in fw.calls if op == "insert"]
    assert inserts.index(gateway_rule_name("block-one")) < inserts.index(gateway_rule_name("bioaf-allow-web"))


def test_gcp_a_lifted_exception_is_lifted_on_the_gateway_after_access_narrows():
    fw = FakeFirewalls([_web(), _deny("block-one", priority=900, sources=("198.51.100.7/32",))])
    _ensure(fw)
    del fw.rules["block-one"]
    fw.rules["bioaf-allow-web"] = _web(sources=("203.0.113.0/24",))
    fw.calls.clear()
    _ensure(fw)
    assert set(_mirrors(fw)) == {gateway_rule_name("bioaf-allow-web")}
    # The allow narrows before the deny it no longer needs is removed.
    assert fw.calls.index(("patch", gateway_rule_name("bioaf-allow-web"))) < fw.calls.index(
        ("delete", gateway_rule_name("block-one"))
    )


def test_gcp_reconciling_never_changes_a_rule_bioaf_does_not_own():
    operator_8443 = _gcp_allow("lab-notebooks", priority=800, ports=("8443",), sources=("10.0.0.0/8",), tags=("bioaf",))
    fw = FakeFirewalls(
        [
            _web(),
            _deny("block-one", priority=900, sources=("198.51.100.7/32",)),
            _web("everything-else", ports=("22", "3306"), sources=("10.0.0.0/8",)),
            operator_8443,
        ]
    )
    before = _effects(fw)
    _ensure(fw)
    _ensure(fw)
    assert _effects(fw) == before
    assert all(name.startswith(GATEWAY_RULE + "-") for _, name in fw.calls)
    for rule in _mirrors(fw).values():
        ports = [(p.I_p_protocol, list(p.ports)) for p in (*rule.allowed, *rule.denied)]
        assert ports == [("tcp", [str(GATEWAY_PORT)])]


def test_gcp_the_single_gateway_rule_from_before_is_replaced_without_a_gap():
    fw = FakeFirewalls([_web()])
    old = _web(GATEWAY_RULE, ports=("8443",))
    fw.rules[GATEWAY_RULE] = old
    _ensure(fw)
    assert set(_mirrors(fw)) == {gateway_rule_name("bioaf-allow-web")}
    assert fw.calls.index(("insert", gateway_rule_name("bioaf-allow-web"))) < fw.calls.index(("delete", GATEWAY_RULE))


def test_gcp_source_selectors_and_destinations_are_kept():
    rule = _web(sources=("10.1.0.0/16",))
    rule.source_tags = ["lab-desktops"]
    rule.source_service_accounts = []
    rule.destination_ranges = ["10.9.0.5/32"]
    fw = FakeFirewalls([rule])
    _ensure(fw)
    mirror = fw.rules[gateway_rule_name("bioaf-allow-web")]
    assert list(mirror.source_tags) == ["lab-desktops"]
    assert list(mirror.destination_ranges) == ["10.9.0.5/32"]


def test_gcp_rules_for_this_hosts_own_tags_and_service_account_apply():
    sa = "bioaf-vm@lab-project.iam.gserviceaccount.com"
    fw = FakeFirewalls(
        [
            _web("default-allow-https", ports=("443",), tags=("https-server",)),
            _for_sa(_web("by-account", ports=("443",), sources=("10.5.0.0/16",)), sa),
            _for_sa(
                _web("other-account", ports=("443",), sources=("10.6.0.0/16",)), "x@lab-project.iam.gserviceaccount.com"
            ),
        ]
    )
    _ensure(fw, HostTargets(tags=("bioaf", "https-server"), service_accounts=(sa,)))
    assert set(_mirrors(fw)) == {gateway_rule_name("default-allow-https"), gateway_rule_name("by-account")}
    assert all(list(r.target_tags) == ["bioaf"] for r in _mirrors(fw).values())


def test_gcp_a_deny_bioaf_cannot_place_is_applied_to_the_gateway():
    """Without this host's service account, a deny for some account fails closed; an allow does not apply."""
    fw = FakeFirewalls(
        [
            _web(),
            _deny(
                "deny-by-account",
                priority=900,
                sources=("0.0.0.0/0",),
                tags=(),
                target_sas=("a@p.iam.gserviceaccount.com",),
            ),
        ]
    )
    _ensure(fw)
    assert gateway_rule_name("deny-by-account") in _mirrors(fw)
    known = FakeFirewalls(
        [
            _web(),
            _deny(
                "deny-by-account",
                priority=900,
                sources=("0.0.0.0/0",),
                tags=(),
                target_sas=("a@p.iam.gserviceaccount.com",),
            ),
        ]
    )
    _ensure(known, HostTargets(tags=("bioaf",), service_accounts=("me@p.iam.gserviceaccount.com",)))
    assert gateway_rule_name("deny-by-account") not in _mirrors(known)


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


def _mark(entries):
    return [{**e, "Description": AWS_GATEWAY_MARK} for e in entries]


def _calls(mock):
    return [c.kwargs for c in mock.call_args_list]


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
    assert _calls(ec2.authorize_security_group_ingress) == [
        {
            "GroupId": "sg-web",
            "IpPermissions": [
                _perm(
                    8443,
                    8443,
                    IpRanges=_mark([{"CidrIp": "0.0.0.0/0"}]),
                    Ipv6Ranges=_mark([{"CidrIpv6": "::/0"}]),
                    PrefixListIds=_mark([{"PrefixListId": "pl-lab"}]),
                )
            ],
        },
    ]
    ec2.revoke_security_group_ingress.assert_not_called()


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


def test_aws_gateway_access_that_covers_only_some_https_sources_is_completed():
    ec2 = _ec2(
        [
            _perm(443, 443, IpRanges=[{"CidrIp": "10.1.0.0/16"}, {"CidrIp": "10.2.0.0/16"}]),
            _perm(8443, 8443, IpRanges=[{"CidrIp": "10.1.0.0/16"}]),  # the operator's own
        ]
    )
    AwsHostNetwork(AWS, client=ec2).ensure_gateway_port()
    assert _calls(ec2.authorize_security_group_ingress) == [
        {"GroupId": "sg-web", "IpPermissions": [_perm(8443, 8443, IpRanges=_mark([{"CidrIp": "10.2.0.0/16"}]))]}
    ]


def test_aws_narrowed_https_narrows_bioafs_gateway_access_and_keeps_the_operators():
    ec2 = _ec2(
        [
            _perm(443, 443, IpRanges=[{"CidrIp": "10.1.0.0/16"}]),
            _perm(
                8443,
                8443,
                IpRanges=[*_mark([{"CidrIp": "0.0.0.0/0"}, {"CidrIp": "10.1.0.0/16"}]), {"CidrIp": "192.0.2.0/24"}],
                Ipv6Ranges=_mark([{"CidrIpv6": "::/0"}]),
            ),
        ]
    )
    AwsHostNetwork(AWS, client=ec2).ensure_gateway_port()
    ec2.authorize_security_group_ingress.assert_not_called()
    assert _calls(ec2.revoke_security_group_ingress) == [
        {
            "GroupId": "sg-web",
            "IpPermissions": [
                _perm(8443, 8443, IpRanges=_mark([{"CidrIp": "0.0.0.0/0"}]), Ipv6Ranges=_mark([{"CidrIpv6": "::/0"}]))
            ],
        }
    ]


def test_aws_closed_https_closes_bioafs_gateway_access():
    ec2 = _ec2([_perm(8443, 8443, IpRanges=_mark([{"CidrIp": "0.0.0.0/0"}]))])
    assert AwsHostNetwork(AWS, client=ec2).ensure_gateway_port() == {"opened": False, "sources": []}
    assert _calls(ec2.revoke_security_group_ingress) == [
        {"GroupId": "sg-web", "IpPermissions": [_perm(8443, 8443, IpRanges=_mark([{"CidrIp": "0.0.0.0/0"}]))]}
    ]


def test_aws_new_access_is_added_before_stale_access_is_removed_and_only_on_the_gateway_port():
    ec2 = _ec2(
        [
            _perm(
                443, 443, IpRanges=[{"CidrIp": "10.1.0.0/16"}], UserIdGroupPairs=[{"GroupId": "sg-lb", "UserId": "1"}]
            ),
            _perm(8443, 8443, IpRanges=_mark([{"CidrIp": "0.0.0.0/0"}])),
        ]
    )
    order = []
    ec2.authorize_security_group_ingress.side_effect = lambda **kw: order.append(("authorize", kw))
    ec2.revoke_security_group_ingress.side_effect = lambda **kw: order.append(("revoke", kw))
    AwsHostNetwork(AWS, client=ec2).ensure_gateway_port()
    assert [op for op, _ in order] == ["authorize", "revoke"]
    added = order[0][1]["IpPermissions"][0]
    assert added["UserIdGroupPairs"] == _mark([{"GroupId": "sg-lb", "UserId": "1"}])
    for _, kw in order:
        assert all((p["FromPort"], p["ToPort"]) == (8443, 8443) for p in kw["IpPermissions"])


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
async def test_on_gcp_the_driver_is_told_this_hosts_own_tags_and_service_accounts(session):
    driver = MagicMock()
    driver.ensure_gateway_port.return_value = {"opened": False, "sources": []}
    targets = HostTargets(tags=("bioaf", "https-server"), service_accounts=("vm@p.iam.gserviceaccount.com",))
    with (
        patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=GCP)),
        patch("app.services.host_ssh_access.network_for", AsyncMock(return_value=driver)),
        patch.object(notebook_gateway_network, "_gcp_host_targets", AsyncMock(return_value=targets)),
    ):
        await notebook_gateway_network.ensure_gateway_port(session)
    driver.ensure_gateway_port.assert_called_once_with(targets)


@pytest.mark.asyncio
async def test_gcp_host_targets_come_from_the_metadata_server_and_fail_closed(monkeypatch):
    import httpx

    def handler(request):
        assert request.headers["Metadata-Flavor"] == "Google"
        if request.url.path.endswith("/instance/tags"):
            return httpx.Response(200, json=["bioaf", "https-server"])
        if request.url.path.endswith("/instance/service-accounts/default/email"):
            return httpx.Response(200, text="vm@p.iam.gserviceaccount.com")
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert await notebook_gateway_network._gcp_host_targets() == HostTargets(
        tags=("bioaf", "https-server"), service_accounts=("vm@p.iam.gserviceaccount.com",)
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kw)
    )
    assert await notebook_gateway_network._gcp_host_targets() is None


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
