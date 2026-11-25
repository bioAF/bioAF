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


KEPT = "The SSH filter could not be applied; previous access was kept."
RESTORE_FAILED = (
    "The SSH filter could not be applied, and bioAF could not confirm the previous rules were put back. "
    "Cloud recovery access is unchanged. Select Check access, then apply again."
)


class FilterApplyError(Exception):
    """The filter could not be applied; the previous access was kept. The message names no secret."""


def _by_family(ranges: list[str]) -> dict[int, list[str]]:
    families: dict[int, list[str]] = {4: [], 6: []}
    for r in ranges:
        families[ipaddress.ip_network(r).version].append(r)
    return families


def _reaches_ssh(protocol: str, ports: list[str]) -> bool:
    if str(protocol) not in ("tcp", "6", "all"):
        return False
    if not ports:
        return True
    for spec in ports:
        low, _, high = str(spec).partition("-")
        if int(low) <= 22 <= int(high or low):
            return True
    return False


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

    def snapshot(self) -> dict:
        """The managed filter rules as they are now, as plain data that can be stored and restored."""
        rules = {}
        for name in (*ALLOW_RULES.values(), *DENY_RULES.values()):
            rule = self._existing(name)
            rules[name] = (
                None
                if rule is None
                else {"priority": rule.priority, "sources": list(rule.source_ranges), "allow": bool(rule.allowed)}
            )
        return {"provider": "gcp", "rules": rules}

    def restore(self, snapshot: dict) -> None:
        """Put the managed filter rules back exactly as ``snapshot`` recorded them."""
        rules = snapshot.get("rules") or {}
        # Recorded rules first (allows, then denies), then remove rules that did not exist.
        for name in (*ALLOW_RULES.values(), *DENY_RULES.values()):
            rule = rules.get(name)
            if rule is not None:
                self._upsert(self._rule(name, priority=rule["priority"], sources=rule["sources"], allow=rule["allow"]))
        for name in (*DENY_RULES.values(), *ALLOW_RULES.values()):
            if rules.get(name) is None:
                self._delete(name)

    def _outranking_allows(self) -> list[str]:
        """Rules that would let SSH from outside reach this host ahead of bioAF's deny.

        GCP applies the highest-priority matching rule (a deny wins a tie), so any enabled allow for
        tcp:22 at a priority above the deny's, targeting this host, from a range outside IAP's
        defeats the filter. A rule targeting service accounts is counted (bioAF cannot tell which
        account the host runs as); source tags are other instances on the network and are left.
        """
        managed = {IAP_RULE, *ALLOW_RULES.values(), *DENY_RULES.values()}
        iap = ipaddress.ip_network(GCP_IAP_SOURCE)
        found = []
        for rule in self.client.list(project=self.project):
            if rule.name in managed or rule.network != self.network or getattr(rule, "disabled", False):
                continue
            if (rule.direction or "INGRESS") != "INGRESS" or not rule.allowed:
                continue
            if rule.priority >= GCP_FILTER_DENY_PRIORITY:
                continue
            tags = list(rule.target_tags or [])
            if tags and self.target_tag not in tags:
                continue
            if not any(_reaches_ssh(a.I_p_protocol, list(a.ports or [])) for a in rule.allowed):
                continue
            outside = [
                s
                for s in rule.source_ranges or []
                if not (ipaddress.ip_network(s).version == 4 and ipaddress.ip_network(s).subnet_of(iap))
            ]
            if outside:
                found.append(rule.name)
        return found

    def apply_filter(self, ranges: list[str]) -> dict:
        blocked = self._outranking_allows()
        if blocked:
            raise FilterApplyError(
                f"The firewall rule {', '.join(blocked)} allows SSH to this server ahead of bioAF's filter, so "
                "the filter would not restrict SSH. Remove or narrow that rule in your cloud console, then "
                "apply again. Previous access was kept."
            )
        families = _by_family(ranges)
        written: list[str] = []
        snapshot = self.snapshot()
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
            logger.warning("Host SSH filter apply failed (%s); restoring the previous rules", type(exc).__name__)
            try:
                self.restore(snapshot)
            except Exception as restore_exc:
                logger.error("Restoring the previous SSH filter rules failed (%s)", type(restore_exc).__name__)
                raise FilterApplyError(RESTORE_FAILED) from exc
            raise FilterApplyError(KEPT) from exc
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

    def _usable_endpoints(self, vpc_id: str) -> list[dict]:
        """Endpoints the recovery rule admits: it allows the endpoint's security group, which is the
        source only when the endpoint does not preserve client addresses (the AWS default)."""
        return [e for e in self._endpoints(vpc_id) if not e.get("PreserveClientIp") and e.get("SecurityGroupIds")]

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
            for endpoint in self._usable_endpoints(instance["VpcId"]):
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
                for e in self._usable_endpoints(instance["VpcId"])
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
        else:
            ready = self._endpoints(instance["VpcId"])
            if not ready:
                problems.append("A private server needs a prepared EC2 Instance Connect Endpoint in its VPC.")
            elif not self._usable_endpoints(instance["VpcId"]):
                ids = ", ".join(e.get("InstanceConnectEndpointId", "") for e in ready)
                problems.append(
                    f"The EC2 Instance Connect Endpoint {ids} keeps client addresses, so bioAF's recovery rule "
                    "does not admit it. Turn off client IP preservation on it, or prepare another endpoint."
                )
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

    def _unsupported_ssh_rules(self, group_ids: list[str]) -> list[str]:
        """Groups with a rule that reaches SSH from outside the VPC in a form bioAF cannot narrow
        without changing other access: all traffic, a TCP port range including 22, or a prefix list."""
        found = []
        for sg in self.client.describe_security_groups(GroupIds=group_ids)["SecurityGroups"] if group_ids else []:
            for perm in sg.get("IpPermissions", []):
                proto = str(perm.get("IpProtocol", ""))
                if proto in ("-1", "all"):
                    covers, exact = True, False
                elif proto in ("tcp", "6"):
                    low, high = perm.get("FromPort", 0), perm.get("ToPort", 65535)
                    covers, exact = low <= 22 <= high, (low, high) == (22, 22)
                else:
                    continue
                if not covers:
                    continue
                outside = perm.get("PrefixListIds") or (not exact and (perm.get("IpRanges") or perm.get("Ipv6Ranges")))
                if outside and sg["GroupId"] not in found:
                    found.append(sg["GroupId"])
        return found

    def _filter_ranges(self, group_id: str) -> tuple[set[str], set[str]]:
        v4: set[str] = set()
        v6: set[str] = set()
        for rule in self._ssh_rules([group_id]):
            for perm in rule["IpPermissions"]:
                v4.update(r["CidrIp"] for r in perm.get("IpRanges", []))
                v6.update(r["CidrIpv6"] for r in perm.get("Ipv6Ranges", []))
        return v4, v6

    def _ssh_permission(self, v4, v6) -> list[dict]:
        if not v4 and not v6:
            return []
        return [
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": c} for c in sorted(v4)],
                "Ipv6Ranges": [{"CidrIpv6": c} for c in sorted(v6)],
            }
        ]

    def snapshot(self) -> dict:
        """SSH access on this server as it is now, as plain data that can be stored and restored:
        the filter group's ranges and attachment, and the other groups' tcp:22 rules."""
        instance = self._instance()
        recovery = self._find_group(AWS_RECOVERY_GROUP, instance["VpcId"])
        filter_group = self._find_group(AWS_FILTER_GROUP, instance["VpcId"])
        attached = [g["GroupId"] for g in instance["SecurityGroups"]]
        v4, v6 = self._filter_ranges(filter_group) if filter_group else (set(), set())
        others = [g for g in attached if g not in (recovery, filter_group)]
        return {
            "provider": "aws",
            "filter_attached": bool(filter_group and filter_group in attached),
            "filter_v4": sorted(v4),
            "filter_v6": sorted(v6),
            "other_ssh": self._ssh_rules(others) if others else [],
        }

    def restore(self, snapshot: dict) -> None:
        """Put SSH access back as ``snapshot`` recorded it: other groups' rules first (access widens
        before anything narrows), then the filter group's ranges and attachment."""
        for rule in snapshot.get("other_ssh") or []:
            present4, present6 = self._filter_ranges(rule["GroupId"])
            perm = rule["IpPermissions"][0]
            missing4 = {r["CidrIp"] for r in perm.get("IpRanges", [])} - present4
            missing6 = {r["CidrIpv6"] for r in perm.get("Ipv6Ranges", [])} - present6
            for missing in self._ssh_permission(missing4, missing6):
                self._authorize(GroupId=rule["GroupId"], IpPermissions=[missing])
        instance = self._instance()
        filter_group = self._find_group(AWS_FILTER_GROUP, instance["VpcId"])
        if not filter_group:
            return
        want4, want6 = set(snapshot.get("filter_v4") or []), set(snapshot.get("filter_v6") or [])
        have4, have6 = self._filter_ranges(filter_group)
        for perm in self._ssh_permission(want4 - have4, want6 - have6):
            self._authorize(GroupId=filter_group, IpPermissions=[perm])
        if snapshot.get("filter_attached"):
            self._attach(filter_group)
        else:
            self._attach("", detach=filter_group)
        for perm in self._ssh_permission(have4 - want4, have6 - want6):
            self.client.revoke_security_group_ingress(GroupId=filter_group, IpPermissions=[perm])

    def apply_filter(self, ranges: list[str]) -> dict:
        snapshot = self.snapshot()
        instance = self._instance()
        families = _by_family(ranges)
        recovery = self._group(AWS_RECOVERY_GROUP, instance["VpcId"])
        filter_group = self._group(AWS_FILTER_GROUP, instance["VpcId"])
        attached_before = [g["GroupId"] for g in instance["SecurityGroups"]]
        others = [g for g in attached_before if g not in (recovery, filter_group)]
        blocked = self._unsupported_ssh_rules(others)
        if blocked:
            raise FilterApplyError(
                f"The security group {', '.join(blocked)} allows SSH through a rule bioAF cannot narrow (all "
                "traffic, a port range that includes 22, or a prefix list). Change that rule in your cloud "
                "console, then apply again. Previous access was kept."
            )
        old4, old6 = self._filter_ranges(filter_group)
        new4, new6 = set(families[4]), set(families[6])
        revoked: list[dict] = []
        try:
            # Allow the new ranges first, attach, then narrow everything else.
            for perm in self._ssh_permission(new4 - old4, new6 - old6):
                self.client.authorize_security_group_ingress(GroupId=filter_group, IpPermissions=[perm])
            self._attach(filter_group)
            for rule in self._ssh_rules(others):
                self.client.revoke_security_group_ingress(**rule)
                revoked.append(rule)
            for perm in self._ssh_permission(old4 - new4, old6 - new6):
                self.client.revoke_security_group_ingress(GroupId=filter_group, IpPermissions=[perm])
        except Exception as exc:
            logger.warning("Host SSH filter apply failed (%s); restoring previous access", type(exc).__name__)
            try:
                self.restore(snapshot)
            except Exception as restore_exc:
                logger.error("Restoring previous SSH access failed (%s)", type(restore_exc).__name__)
                raise FilterApplyError(RESTORE_FAILED) from exc
            raise FilterApplyError(KEPT) from exc
        return {"provider": "aws", "revoked": revoked, "filter_group": filter_group}

    def _authorize(self, **rule) -> None:
        try:
            self.client.authorize_security_group_ingress(**rule)
        except Exception as exc:  # an identical existing rule is fine
            if "Duplicate" not in str(exc):
                raise

    def remove_filter(self, record: dict | None = None) -> None:
        record = record or {}
        # Restore what the filter revoked before detaching the filter group.
        for rule in record.get("revoked", []):
            self._authorize(**rule)
        if record.get("filter_group"):
            self._attach("", detach=record["filter_group"])
