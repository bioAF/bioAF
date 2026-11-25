"""Plan 03B: the application-host SSH filter and its protected cloud recovery sources.

Provider clients are mocked: these prove the rules bioAF writes, their order, scope and rollback,
not that a live cloud accepts them (provider acceptance is a separate release gate).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.host_ssh_filter import AwsHostNetwork, FilterApplyError, GcpHostNetwork
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
)


class FakeFirewalls:
    """An in-memory compute_v1.FirewallsClient keyed by rule name."""

    def __init__(self, existing=None, fail_on=None):
        self.rules = {r.name: r for r in (existing or [])}
        self.calls = []
        self.fail_on = fail_on

    def _op(self):
        return SimpleNamespace(result=lambda timeout=None: None)

    def list(self, project):
        return list(self.rules.values())

    def get(self, project, firewall):
        from google.api_core.exceptions import NotFound

        if firewall not in self.rules:
            raise NotFound("no rule")
        return self.rules[firewall]

    def insert(self, project, firewall_resource):
        self.calls.append(("insert", firewall_resource.name))
        if self.fail_on and self.fail_on in firewall_resource.name:
            raise RuntimeError("quota")
        self.rules[firewall_resource.name] = firewall_resource
        return self._op()

    def patch(self, project, firewall, firewall_resource):
        self.calls.append(("patch", firewall))
        self.rules[firewall] = firewall_resource
        return self._op()

    def delete(self, project, firewall):
        self.calls.append(("delete", firewall))
        self.rules.pop(firewall, None)
        return self._op()


def _rule_summary(rule):
    allowed = [(a.I_p_protocol, list(a.ports)) for a in rule.allowed]
    denied = [(d.I_p_protocol, list(d.ports)) for d in rule.denied]
    return {
        "priority": rule.priority,
        "sources": sorted(rule.source_ranges),
        "targets": list(rule.target_tags),
        "allowed": allowed,
        "denied": denied,
        "network": rule.network,
    }


def test_gcp_recovery_rule_is_created_at_priority_850_for_this_host_only():
    fw = FakeFirewalls()
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    sources = net.ensure_recovery()
    rule = fw.rules["bioaf-ssh-recovery-iap"]
    assert _rule_summary(rule) == {
        "priority": 850,
        "sources": ["35.235.240.0/20"],
        "targets": ["bioaf"],
        "allowed": [("tcp", ["22"])],
        "denied": [],
        "network": "projects/lab-project/global/networks/default",
    }
    assert sources == [{"kind": "gcp_iap", "source": "35.235.240.0/20", "priority": 850, "ports": "tcp:22"}]


def test_gcp_recovery_drift_is_repaired():
    fw = FakeFirewalls()
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    fw.rules["bioaf-ssh-recovery-iap"].priority = 1000
    net.ensure_recovery()
    assert fw.rules["bioaf-ssh-recovery-iap"].priority == 850
    assert ("patch", "bioaf-ssh-recovery-iap") in fw.calls


def test_gcp_filter_adds_allows_before_separate_v4_and_v6_denies():
    fw = FakeFirewalls()
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    net.apply_filter(["203.0.113.7/32", "2001:db8::/32"])
    order = [name for kind, name in fw.calls if kind == "insert"]
    assert order.index("bioaf-ssh-filter-allow-v4") < order.index("bioaf-ssh-filter-deny-v4")
    assert order.index("bioaf-ssh-filter-allow-v6") < order.index("bioaf-ssh-filter-deny-v6")
    assert _rule_summary(fw.rules["bioaf-ssh-filter-allow-v4"])["sources"] == ["203.0.113.7/32"]
    assert _rule_summary(fw.rules["bioaf-ssh-filter-allow-v4"])["priority"] == 875
    assert _rule_summary(fw.rules["bioaf-ssh-filter-allow-v6"])["sources"] == ["2001:db8::/32"]
    assert _rule_summary(fw.rules["bioaf-ssh-filter-deny-v4"]) | {} == {
        "priority": 1000,
        "sources": ["0.0.0.0/0"],
        "targets": ["bioaf"],
        "allowed": [],
        "denied": [("tcp", ["22"])],
        "network": "projects/lab-project/global/networks/default",
    }
    assert _rule_summary(fw.rules["bioaf-ssh-filter-deny-v6"])["sources"] == ["::/0"]
    # Recovery stays above the deny and is never part of the editable rules.
    assert fw.rules["bioaf-ssh-recovery-iap"].priority < 875


def test_gcp_failed_apply_removes_its_partial_rules_and_keeps_recovery():
    fw = FakeFirewalls(fail_on="deny-v4")
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    with pytest.raises(FilterApplyError):
        net.apply_filter(["203.0.113.7/32"])
    assert set(fw.rules) == {"bioaf-ssh-recovery-iap"}


def test_gcp_removing_the_filter_keeps_recovery():
    fw = FakeFirewalls()
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    net.apply_filter(["203.0.113.7/32"])
    net.remove_filter()
    assert set(fw.rules) == {"bioaf-ssh-recovery-iap"}


def test_gcp_a_higher_priority_deny_blocking_recovery_is_reported():
    from google.cloud import compute_v1

    blocker = compute_v1.Firewall(
        name="corp-deny-ssh",
        priority=100,
        direction="INGRESS",
        network="projects/lab-project/global/networks/default",
        source_ranges=["0.0.0.0/0"],
        denied=[compute_v1.Denied(I_p_protocol="tcp", ports=["22"])],
    )
    fw = FakeFirewalls(existing=[blocker])
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    problems = net.check_recovery()
    assert any("corp-deny-ssh" in p for p in problems)


# -- AWS -----------------------------------------------------------------------------------------

AWS = HostIdentity(
    provider="aws",
    account="123456789012",
    region="us-west-1",
    zone="us-west-1b",
    instance_id="i-0abc123def4567890",
    instance_name="bioaf",
    public=True,
    os_username="ubuntu",
)


def _ec2(groups=("sg-base",), base_ssh=("0.0.0.0/0",), endpoints=(), base_extra=()):
    ec2 = MagicMock()
    state = {"attached": list(groups), "sgs": {"sg-base": {"ssh": list(base_ssh), "extra": list(base_extra)}}}
    ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": AWS.instance_id,
                        "VpcId": "vpc-1",
                        "SubnetId": "subnet-1",
                        "PublicIpAddress": "54.1.2.3" if AWS.public else None,
                        "SecurityGroups": [{"GroupId": g} for g in state["attached"]],
                    }
                ]
            }
        ]
    }
    ec2.describe_managed_prefix_lists.return_value = {
        "PrefixLists": [{"PrefixListId": "pl-eic4", "PrefixListName": "com.amazonaws.us-west-1.ec2-instance-connect"}]
    }

    def describe_sgs(Filters=None, GroupIds=None):
        if GroupIds:
            return {
                "SecurityGroups": [
                    {
                        "GroupId": g,
                        "IpPermissions": [
                            {
                                "IpProtocol": "tcp",
                                "FromPort": 22,
                                "ToPort": 22,
                                "IpRanges": [{"CidrIp": c} for c in state["sgs"].get(g, {}).get("ssh", [])],
                                "Ipv6Ranges": [],
                            }
                        ]
                        + list(state["sgs"].get(g, {}).get("extra", [])),
                    }
                    for g in GroupIds
                ]
            }
        name = next(f["Values"][0] for f in Filters if f["Name"] == "group-name")
        found = [{"GroupId": gid, "GroupName": name} for gid, sg in state["sgs"].items() if sg.get("name") == name]
        return {"SecurityGroups": found}

    def create_sg(GroupName, Description, VpcId):
        gid = f"sg-{GroupName}"
        state["sgs"][gid] = {"name": GroupName, "ssh": []}
        return {"GroupId": gid}

    def modify(InstanceId, Groups):
        state["attached"] = list(Groups)
        ec2.describe_instances.return_value["Reservations"][0]["Instances"][0]["SecurityGroups"] = [
            {"GroupId": g} for g in Groups
        ]

    def revoke(GroupId, IpPermissions):
        for perm in IpPermissions:
            for r in perm.get("IpRanges", []):
                state["sgs"][GroupId]["ssh"].remove(r["CidrIp"])

    def authorize(GroupId, IpPermissions):
        for perm in IpPermissions:
            for r in perm.get("IpRanges", []):
                state["sgs"].setdefault(GroupId, {"ssh": []})["ssh"].append(r["CidrIp"])

    ec2.describe_security_groups.side_effect = describe_sgs
    ec2.create_security_group.side_effect = create_sg
    ec2.modify_instance_attribute.side_effect = modify
    ec2.revoke_security_group_ingress.side_effect = revoke
    ec2.authorize_security_group_ingress.side_effect = authorize
    ec2.describe_instance_connect_endpoints.return_value = {"InstanceConnectEndpoints": list(endpoints)}
    return ec2, state


def test_aws_recovery_uses_the_regions_managed_prefix_list_and_is_attached():
    ec2, state = _ec2()
    net = AwsHostNetwork(AWS, client=ec2)
    sources = net.ensure_recovery()
    perms = ec2.authorize_security_group_ingress.call_args.kwargs["IpPermissions"]
    assert perms == [
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "PrefixListIds": [{"PrefixListId": "pl-eic4"}]}
    ]
    assert "sg-bioaf-ssh-recovery" in state["attached"] and "sg-base" in state["attached"]
    assert sources[0]["kind"] == "aws_eic_prefix_list" and sources[0]["source"] == "pl-eic4"


def test_aws_private_host_without_a_prepared_endpoint_needs_attention():
    from dataclasses import replace

    ec2, _ = _ec2()
    net = AwsHostNetwork(replace(AWS, public=False), client=ec2)
    net.ensure_recovery()
    assert any("EC2 Instance Connect Endpoint" in p for p in net.check_recovery())


def test_aws_filter_narrows_ssh_and_rollback_restores_what_it_revoked():
    ec2, state = _ec2()
    net = AwsHostNetwork(AWS, client=ec2)
    net.ensure_recovery()
    record = net.apply_filter(["203.0.113.7/32"])
    assert state["sgs"]["sg-base"]["ssh"] == []
    assert state["sgs"]["sg-bioaf-ssh-filter"]["ssh"] == ["203.0.113.7/32"]
    assert "sg-bioaf-ssh-recovery" in state["attached"]
    net.remove_filter(record)
    assert state["sgs"]["sg-base"]["ssh"] == ["0.0.0.0/0"]
    assert "sg-bioaf-ssh-filter" not in state["attached"]
    assert "sg-bioaf-ssh-recovery" in state["attached"]


# -- Review findings: edits, failed edits, unsupported rules, endpoint client IP preservation ------


def test_gcp_a_failed_edit_restores_the_previous_rules_instead_of_opening_ssh():
    fw = FakeFirewalls()
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    net.apply_filter(["203.0.113.7/32"])
    before = {name: _rule_summary(rule) for name, rule in fw.rules.items()}
    fw.fail_on = "allow-v6"
    with pytest.raises(FilterApplyError, match="previous access was kept"):
        net.apply_filter(["2001:db8::/32"])
    assert {name: _rule_summary(rule) for name, rule in fw.rules.items()} == before


def test_aws_editing_the_ranges_removes_the_replaced_address():
    ec2, state = _ec2()
    net = AwsHostNetwork(AWS, client=ec2)
    net.ensure_recovery()
    net.apply_filter(["203.0.113.7/32"])
    net.apply_filter(["198.51.100.9/32"])
    assert state["sgs"]["sg-bioaf-ssh-filter"]["ssh"] == ["198.51.100.9/32"]


def test_aws_a_rule_bioaf_cannot_narrow_refuses_the_filter_and_keeps_access():
    for extra in (
        {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
        {"IpProtocol": "tcp", "FromPort": 0, "ToPort": 65535, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "PrefixListIds": [{"PrefixListId": "pl-corp"}]},
    ):
        ec2, state = _ec2(base_ssh=(), base_extra=[extra])
        net = AwsHostNetwork(AWS, client=ec2)
        net.ensure_recovery()
        with pytest.raises(FilterApplyError, match="sg-base"):
            net.apply_filter(["203.0.113.7/32"])
        ec2.revoke_security_group_ingress.assert_not_called()
        assert "sg-bioaf-ssh-filter" not in state["attached"]
        assert state["sgs"]["sg-base"]["extra"] == [extra]


def test_aws_a_rule_that_does_not_reach_ssh_is_left_alone():
    extra = {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ec2, state = _ec2(base_extra=[extra])
    net = AwsHostNetwork(AWS, client=ec2)
    net.ensure_recovery()
    net.apply_filter(["203.0.113.7/32"])
    assert state["sgs"]["sg-base"]["ssh"] == []
    assert state["sgs"]["sg-base"]["extra"] == [extra]


def test_aws_a_failed_edit_keeps_the_previous_filter_attached():
    ec2, state = _ec2()
    net = AwsHostNetwork(AWS, client=ec2)
    net.ensure_recovery()
    net.apply_filter(["203.0.113.7/32"])
    authorize = ec2.authorize_security_group_ingress.side_effect
    ec2.authorize_security_group_ingress.side_effect = RuntimeError("limit")
    with pytest.raises(FilterApplyError, match="previous access was kept"):
        net.apply_filter(["198.51.100.9/32"])
    ec2.authorize_security_group_ingress.side_effect = authorize
    assert state["sgs"]["sg-bioaf-ssh-filter"]["ssh"] == ["203.0.113.7/32"]
    assert "sg-bioaf-ssh-filter" in state["attached"]
    assert state["sgs"]["sg-base"]["ssh"] == []


def test_aws_private_recovery_needs_an_endpoint_that_does_not_preserve_client_addresses():
    from dataclasses import replace

    endpoint = {
        "InstanceConnectEndpointId": "eice-1",
        "State": "create-complete",
        "SecurityGroupIds": ["sg-eice"],
        "PreserveClientIp": True,
    }
    ec2, _ = _ec2(endpoints=[endpoint])
    net = AwsHostNetwork(replace(AWS, public=False), client=ec2)
    net.ensure_recovery()
    assert any("eice-1" in p and "client" in p for p in net.check_recovery())

    ec2, _ = _ec2(endpoints=[{**endpoint, "PreserveClientIp": False}])
    net = AwsHostNetwork(replace(AWS, public=False), client=ec2)
    net.ensure_recovery()
    assert net.check_recovery() == []


def _gcp_allow(name, *, priority, sources=("0.0.0.0/0",), ports=("22",), protocol="tcp", tags=(), disabled=False):
    from google.cloud import compute_v1

    return compute_v1.Firewall(
        name=name,
        priority=priority,
        direction="INGRESS",
        network="projects/lab-project/global/networks/default",
        source_ranges=list(sources),
        target_tags=list(tags),
        disabled=disabled,
        allowed=[compute_v1.Allowed(I_p_protocol=protocol, ports=list(ports))],
    )


def test_gcp_an_allow_that_outranks_the_deny_refuses_the_filter_and_changes_nothing():
    """GCP applies the highest-priority matching rule, so an SSH allow above 1000 defeats the deny."""
    for rule in (
        _gcp_allow("corp-ssh", priority=900),
        _gcp_allow("all-tcp", priority=500, ports=(), tags=("bioaf",)),
        _gcp_allow("range", priority=999, ports=("20-30",)),
        _gcp_allow("everything", priority=999, protocol="all", ports=()),
    ):
        fw = FakeFirewalls(existing=[rule])
        net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
        net.ensure_recovery()
        with pytest.raises(FilterApplyError, match=rule.name):
            net.apply_filter(["203.0.113.7/32"])
        assert set(fw.rules) == {rule.name, "bioaf-ssh-recovery-iap"}


def test_gcp_allows_the_deny_already_outranks_or_that_miss_this_host_are_left_alone():
    for rule in (
        _gcp_allow("default-allow-ssh", priority=65534),
        _gcp_allow("other-host", priority=900, tags=("db",)),
        _gcp_allow("web", priority=900, ports=("443",)),
        _gcp_allow("off", priority=900, disabled=True),
        # At equal priority GCP applies the deny.
        _gcp_allow("tie", priority=1000),
    ):
        fw = FakeFirewalls(existing=[rule])
        net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
        net.ensure_recovery()
        net.apply_filter(["203.0.113.7/32"])
        assert "bioaf-ssh-filter-deny-v4" in fw.rules


def test_gcp_snapshot_and_restore_round_trip_the_managed_rules():
    import json

    fw = FakeFirewalls()
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    net.apply_filter(["203.0.113.7/32"])
    before = {name: _rule_summary(rule) for name, rule in fw.rules.items()}
    snapshot = json.loads(json.dumps(net.snapshot()))
    net.apply_filter(["2001:db8::/32"])
    net.restore(snapshot)
    assert {name: _rule_summary(rule) for name, rule in fw.rules.items()} == before


def test_aws_snapshot_and_restore_round_trip_ssh_access():
    import json

    ec2, state = _ec2()
    net = AwsHostNetwork(AWS, client=ec2)
    net.ensure_recovery()
    snapshot = json.loads(json.dumps(net.snapshot()))
    net.apply_filter(["203.0.113.7/32"])
    net.restore(snapshot)
    assert state["sgs"]["sg-base"]["ssh"] == ["0.0.0.0/0"]
    assert "sg-bioaf-ssh-filter" not in state["attached"]
    assert state["sgs"]["sg-bioaf-ssh-filter"]["ssh"] == []
    assert "sg-bioaf-ssh-recovery" in state["attached"]


# -- Not set up versus changed (owner decision 2026-11-25) -------------------------------------------


def test_gcp_recovery_never_set_up_is_missing_not_a_problem():
    fw = FakeFirewalls()
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    assert net.recovery_missing() is True
    assert net.check_recovery() == []


def test_gcp_recovery_changed_outside_bioaf_is_a_problem_not_missing():
    fw = FakeFirewalls()
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    assert net.recovery_missing() is False
    assert net.check_recovery() == []
    fw.rules["bioaf-ssh-recovery-iap"].priority = 1000
    assert net.check_recovery() == ["The IAP recovery rule was changed outside bioAF."]


def test_aws_recovery_never_set_up_is_missing_not_a_problem():
    ec2, _ = _ec2()
    net = AwsHostNetwork(AWS, client=ec2)
    assert net.recovery_missing() is True
    assert not any("security group" in p for p in net.check_recovery())
    net.ensure_recovery()
    assert net.recovery_missing() is False
