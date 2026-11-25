"""Application-host SSH network filter and protected cloud recovery sources (plan 03B).

The backend's infrastructure/network service owns these rules on existing installations, using its
configured provider authority (GCP bootstrap impersonation through ``load_gcp_credentials``; the
AWS instance profile). It writes network rules only: no operator shell, no key delivery, no IAM.

GCP, targeting only this installation's application-host tag on its network, one address family
per rule:

- ``bioaf-ssh-recovery-iap``: allow tcp:22 from IAP's 35.235.240.0/20 at priority 850, always;
- ``bioaf-ssh-filter-allow-v4`` / ``-v6``: allow tcp:22 from the editable ranges at 875;
- ``bioaf-ssh-filter-deny-v4`` / ``-v6``: deny tcp:22 from everywhere at 1000.

AWS, on the application instance's own VPC:

- ``bioaf-ssh-recovery``: a security group allowing tcp:22 from the Region's AWS-managed
  EC2 Instance Connect prefix list (and, for a private host, the prepared endpoint's group),
  always attached;
- ``bioaf-ssh-filter``: a security group with the editable ranges; applying the filter revokes
  tcp:22 from the instance's other groups and records exactly what it revoked, so turning the
  filter off or rolling back restores it.

Allows are written before denies or revocations, so the last working route is never removed
first; a failure removes this apply's partial rules and keeps the recovery sources. The protected
sources are application-validated and reconciled: anyone holding the same cloud authority (this
backend included) could still delete them, which ``check_recovery`` reports as drift.
"""

from __future__ import annotations

import ipaddress
import logging

from app.services.ssh_recovery import (
    GCP_FILTER_ALLOW_PRIORITY,
    GCP_FILTER_DENY_PRIORITY,
    GCP_IAP_PRIORITY,
    GCP_IAP_SOURCE,
)

logger = logging.getLogger("bioaf.host_ssh_filter")

IAP_RULE = "bioaf-ssh-recovery-iap"
ALLOW_RULES = {4: "bioaf-ssh-filter-allow-v4", 6: "bioaf-ssh-filter-allow-v6"}
DENY_RULES = {4: "bioaf-ssh-filter-deny-v4", 6: "bioaf-ssh-filter-deny-v6"}
EVERYWHERE = {4: "0.0.0.0/0", 6: "::/0"}
AWS_RECOVERY_GROUP = "bioaf-ssh-recovery"
AWS_FILTER_GROUP = "bioaf-ssh-filter"


class FilterApplyError(Exception):
    """The filter could not be applied; the previous access was kept. The message names no secret."""


def _by_family(ranges: list[str]) -> dict[int, list[str]]:
    families: dict[int, list[str]] = {4: [], 6: []}
    for r in ranges:
        families[ipaddress.ip_network(r).version].append(r)
    return families


class GcpHostNetwork:
    def __init__(self, identity, *, client, target_tag: str = "bioaf"):
        self.identity = identity
        self.client = client
        self.project = identity.account
        self.network = f"projects/{identity.account}/global/networks/{identity.network or 'default'}"
        self.target_tag = target_tag

    def _rule(self, name, *, priority, sources, allow: bool):
        from google.cloud import compute_v1

        rule = compute_v1.Firewall(
            name=name,
            network=self.network,
            direction="INGRESS",
            priority=priority,
            source_ranges=list(sources),
            target_tags=[self.target_tag],
            description="Managed by bioAF (plan 03B): application-host SSH access.",
        )
        port = [compute_v1.Allowed(I_p_protocol="tcp", ports=["22"])] if allow else []
        if allow:
            rule.allowed = port
        else:
            rule.denied = [compute_v1.Denied(I_p_protocol="tcp", ports=["22"])]
        return rule

    def _existing(self, name):
        from google.api_core.exceptions import NotFound

        try:
            return self.client.get(project=self.project, firewall=name)
        except NotFound:
            return None

    def _upsert(self, rule) -> None:
        current = self._existing(rule.name)
        if current is None:
            self.client.insert(project=self.project, firewall_resource=rule).result(timeout=120)
        elif (
            current.priority != rule.priority
            or sorted(current.source_ranges) != sorted(rule.source_ranges)
            or list(current.target_tags) != list(rule.target_tags)
            or len(current.allowed) != len(rule.allowed)
            or len(current.denied) != len(rule.denied)
        ):
            self.client.patch(project=self.project, firewall=rule.name, firewall_resource=rule).result(timeout=120)

    def _delete(self, name) -> None:
        if self._existing(name) is not None:
            self.client.delete(project=self.project, firewall=name).result(timeout=120)

    def recovery_sources(self) -> list[dict]:
        return [{"kind": "gcp_iap", "source": GCP_IAP_SOURCE, "priority": GCP_IAP_PRIORITY, "ports": "tcp:22"}]

    def ensure_recovery(self) -> list[dict]:
        self._upsert(self._rule(IAP_RULE, priority=GCP_IAP_PRIORITY, sources=[GCP_IAP_SOURCE], allow=True))
        return self.recovery_sources()

    def check_recovery(self) -> list[str]:
        problems = []
        iap = self._existing(IAP_RULE)
        if iap is None or iap.priority != GCP_IAP_PRIORITY or list(iap.source_ranges) != [GCP_IAP_SOURCE]:
            problems.append("The IAP recovery rule is missing or was changed outside bioAF.")
        iap_net = ipaddress.ip_network(GCP_IAP_SOURCE)
        for rule in self.client.list(project=self.project):
            if rule.name == IAP_RULE or rule.network != self.network or (rule.direction or "INGRESS") != "INGRESS":
                continue
            if not rule.denied or rule.priority > GCP_IAP_PRIORITY:
                continue
            targets_us = not rule.target_tags or self.target_tag in rule.target_tags
            covers_ssh = any(d.I_p_protocol in ("tcp", "all") and (not d.ports or "22" in d.ports) for d in rule.denied)
            covers_iap = any(
                ipaddress.ip_network(s).version == 4 and iap_net.subnet_of(ipaddress.ip_network(s))
                for s in rule.source_ranges
            )
            if targets_us and covers_ssh and covers_iap:
                problems.append(f"The firewall rule {rule.name} denies SSH from IAP ahead of bioAF's recovery rule.")
        return problems

    def apply_filter(self, ranges: list[str]) -> dict:
        families = _by_family(ranges)
        written: list[str] = []
        try:
            for version in (4, 6):
                if families[version]:
                    self._upsert(
                        self._rule(
                            ALLOW_RULES[version],
                            priority=GCP_FILTER_ALLOW_PRIORITY,
                            sources=families[version],
                            allow=True,
                        )
                    )
                    written.append(ALLOW_RULES[version])
                else:
                    self._delete(ALLOW_RULES[version])
            for version in (4, 6):
                self._upsert(
                    self._rule(
                        DENY_RULES[version],
                        priority=GCP_FILTER_DENY_PRIORITY,
                        sources=[EVERYWHERE[version]],
                        allow=False,
                    )
                )
                written.append(DENY_RULES[version])
        except Exception as exc:
            logger.warning("Host SSH filter apply failed (%s); removing partial rules", type(exc).__name__)
            self.remove_filter()
            raise FilterApplyError("The SSH filter could not be applied; previous access was kept.") from exc
        return {"provider": "gcp", "rules": written}

    def remove_filter(self, record: dict | None = None) -> None:
        # Denies first, so access widens before any allow disappears.
        for name in (*DENY_RULES.values(), *ALLOW_RULES.values()):
            self._delete(name)


class AwsHostNetwork:
    def __init__(self, identity, *, client):
        self.identity = identity
        self.client = client

    def _instance(self) -> dict:
        resp = self.client.describe_instances(InstanceIds=[self.identity.instance_id])
        return resp["Reservations"][0]["Instances"][0]

    def _find_group(self, name: str, vpc_id: str) -> str | None:
        found = self.client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [name]}, {"Name": "vpc-id", "Values": [vpc_id]}]
        )["SecurityGroups"]
        return found[0]["GroupId"] if found else None

    def _group(self, name: str, vpc_id: str) -> str:
        found = self._find_group(name, vpc_id)
        if found:
            return found
        return self.client.create_security_group(
            GroupName=name, Description="Managed by bioAF (plan 03B): application-host SSH access", VpcId=vpc_id
        )["GroupId"]

    def _attach(self, group_id: str, *, detach: str | None = None) -> None:
        current = [g["GroupId"] for g in self._instance()["SecurityGroups"]]
        wanted = [g for g in current if g != detach]
        if group_id and group_id not in wanted:
            wanted.append(group_id)
        if wanted != current:
            self.client.modify_instance_attribute(InstanceId=self.identity.instance_id, Groups=wanted)

    def _prefix_lists(self) -> list[str]:
        names = [
            f"com.amazonaws.{self.identity.region}.ec2-instance-connect",
            f"com.amazonaws.{self.identity.region}.ipv6.ec2-instance-connect",
        ]
        lists = self.client.describe_managed_prefix_lists(Filters=[{"Name": "prefix-list-name", "Values": names}])
        return [p["PrefixListId"] for p in lists.get("PrefixLists", [])]

    def _endpoints(self, vpc_id: str) -> list[dict]:
        try:
            resp = self.client.describe_instance_connect_endpoints(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
        except Exception:
            return []
        return [e for e in resp.get("InstanceConnectEndpoints", []) if e.get("State") in (None, "create-complete")]

    def recovery_sources(self) -> list[dict]:
        return [{"kind": "aws_eic_prefix_list", "source": pl, "ports": "tcp:22"} for pl in self._prefix_lists()]

    def ensure_recovery(self) -> list[dict]:
        instance = self._instance()
        group = self._group(AWS_RECOVERY_GROUP, instance["VpcId"])
        permissions = []
        if self.identity.public:
            permissions = [
                {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "PrefixListIds": [{"PrefixListId": pl}]}
                for pl in self._prefix_lists()
            ]
        else:
            for endpoint in self._endpoints(instance["VpcId"]):
                for sg in endpoint.get("SecurityGroupIds", []):
                    permissions.append(
                        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "UserIdGroupPairs": [{"GroupId": sg}]}
                    )
        for perm in permissions:
            try:
                self.client.authorize_security_group_ingress(GroupId=group, IpPermissions=[perm])
            except Exception as exc:  # an existing identical rule is fine
                if "Duplicate" not in str(exc):
                    raise
        self._attach(group)
        return (
            self.recovery_sources()
            if self.identity.public
            else [
                {"kind": "aws_eic_endpoint", "source": e.get("InstanceConnectEndpointId", ""), "ports": "tcp:22"}
                for e in self._endpoints(instance["VpcId"])
            ]
        )

    def check_recovery(self) -> list[str]:
        problems = []
        instance = self._instance()
        attached = [g["GroupId"] for g in instance["SecurityGroups"]]
        recovery = self._find_group(AWS_RECOVERY_GROUP, instance["VpcId"])
        if recovery is None or recovery not in attached:
            problems.append("The EC2 Instance Connect recovery security group is not attached to this server.")
        if self.identity.public:
            if not instance.get("PublicIpAddress"):
                problems.append("This server has no public address for EC2 Instance Connect.")
            if not self._prefix_lists():
                problems.append("The Region's EC2 Instance Connect prefix list could not be found.")
        elif not self._endpoints(instance["VpcId"]):
            problems.append("A private server needs a prepared EC2 Instance Connect Endpoint in its VPC.")
        return problems

    def _ssh_rules(self, group_ids: list[str]) -> list[dict]:
        revoked = []
        for sg in self.client.describe_security_groups(GroupIds=group_ids)["SecurityGroups"]:
            for perm in sg.get("IpPermissions", []):
                if perm.get("FromPort") == 22 and perm.get("ToPort") == 22 and perm.get("IpProtocol") == "tcp":
                    ranges = [{"CidrIp": r["CidrIp"]} for r in perm.get("IpRanges", [])]
                    ranges6 = [{"CidrIpv6": r["CidrIpv6"]} for r in perm.get("Ipv6Ranges", [])]
                    if ranges or ranges6:
                        revoked.append(
                            {
                                "GroupId": sg["GroupId"],
                                "IpPermissions": [
                                    {
                                        "IpProtocol": "tcp",
                                        "FromPort": 22,
                                        "ToPort": 22,
                                        "IpRanges": ranges,
                                        "Ipv6Ranges": ranges6,
                                    }
                                ],
                            }
                        )
        return revoked

    def apply_filter(self, ranges: list[str]) -> dict:
        instance = self._instance()
        families = _by_family(ranges)
        recovery = self._group(AWS_RECOVERY_GROUP, instance["VpcId"])
        filter_group = self._group(AWS_FILTER_GROUP, instance["VpcId"])
        revoked: list[dict] = []
        try:
            self.client.authorize_security_group_ingress(
                GroupId=filter_group,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 22,
                        "ToPort": 22,
                        "IpRanges": [{"CidrIp": c} for c in families[4]],
                        "Ipv6Ranges": [{"CidrIpv6": c} for c in families[6]],
                    }
                ],
            )
            self._attach(filter_group)
            others = [
                g["GroupId"] for g in self._instance()["SecurityGroups"] if g["GroupId"] not in (recovery, filter_group)
            ]
            for rule in self._ssh_rules(others):
                self.client.revoke_security_group_ingress(**rule)
                revoked.append(rule)
        except Exception as exc:
            logger.warning("Host SSH filter apply failed (%s); restoring previous access", type(exc).__name__)
            self.remove_filter({"revoked": revoked, "filter_group": filter_group})
            raise FilterApplyError("The SSH filter could not be applied; previous access was kept.") from exc
        return {"provider": "aws", "revoked": revoked, "filter_group": filter_group}

    def remove_filter(self, record: dict | None = None) -> None:
        record = record or {}
        # Restore what the filter revoked before detaching the filter group.
        for rule in record.get("revoked", []):
            try:
                self.client.authorize_security_group_ingress(**rule)
            except Exception as exc:
                if "Duplicate" not in str(exc):
                    raise
        if record.get("filter_group"):
            self._attach("", detach=record["filter_group"])
