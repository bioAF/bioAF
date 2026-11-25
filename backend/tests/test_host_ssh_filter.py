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
    provider="gcp", account="lab-project", region="us-central1", zone="us-central1-b",
    instance_id="481516", instance_name="bioaf", public=True, network="default",
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
        "priority": 1000, "sources": ["0.0.0.0/0"], "targets": ["bioaf"], "allowed": [], "denied": [("tcp", ["22"])],
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
        name="corp-deny-ssh", priority=100, direction="INGRESS", network="projects/lab-project/global/networks/default",
        source_ranges=["0.0.0.0/0"], denied=[compute_v1.Denied(I_p_protocol="tcp", ports=["22"])],
    )
    fw = FakeFirewalls(existing=[blocker])
    net = GcpHostNetwork(GCP, client=fw, target_tag="bioaf")
    net.ensure_recovery()
    problems = net.check_recovery()
    assert any("corp-deny-ssh" in p for p in problems)


# -- AWS -----------------------------------------------------------------------------------------

AWS = HostIdentity(
    provider="aws", account="123456789012", region="us-west-1", zone="us-west-1b",
    instance_id="i-0abc123def4567890", instance_name="bioaf", public=True, os_username="ubuntu",
)


def _ec2(groups=("sg-base",), base_ssh=("0.0.0.0/0",), endpoints=()):
    ec2 = MagicMock()
    state = {"attached": list(groups), "sgs": {"sg-base": {"ssh": list(base_ssh)}}}
    ec2.describe_instances.return_value = {
        "Reservations": [{"Instances": [{
            "InstanceId": AWS.instance_id, "VpcId": "vpc-1", "SubnetId": "subnet-1",
            "PublicIpAddress": "54.1.2.3" if AWS.public else None,
            "SecurityGroups": [{"GroupId": g} for g in state["attached"]],
        }]}]
    }
    ec2.describe_managed_prefix_lists.return_value = {
        "PrefixLists": [{"PrefixListId": "pl-eic4", "PrefixListName": "com.amazonaws.us-west-1.ec2-instance-connect"}]
    }

    def describe_sgs(Filters=None, GroupIds=None):
        if GroupIds:
            return {"SecurityGroups": [{
                "GroupId": g,
                "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": c} for c in state["sgs"].get(g, {}).get("ssh", [])], "Ipv6Ranges": []}],
            } for g in GroupIds]}
        name = next(f["Values"][0] for f in Filters if f["Name"] == "group-name")
        found = [{"GroupId": gid, "GroupName": name} for gid, sg in state["sgs"].items() if sg.get("name") == name]
        return {"SecurityGroups": found}

    def create_sg(GroupName, Description, VpcId):
        gid = f"sg-{GroupName}"
        state["sgs"][gid] = {"name": GroupName, "ssh": []}
        return {"GroupId": gid}

    def modify(InstanceId, Groups):
        state["attached"] = list(Groups)
        ec2.describe_instances.return_value["Reservations"][0]["Instances"][0]["SecurityGroups"] = [{"GroupId": g} for g in Groups]

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
    assert perms == [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "PrefixListIds": [{"PrefixListId": "pl-eic4"}]}]
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
