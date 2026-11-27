"""Application-host SSH network filter and protected cloud recovery sources (plan 03B).

The provider side (adapter); app.services.host_ssh_access owns the lifecycle.

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

import hashlib
import ipaddress
import logging
from dataclasses import dataclass

logger = logging.getLogger("bioaf.host_ssh_filter")

# GCP firewall priorities: IAP recovery above the editable allows, both above the default deny.
GCP_IAP_SOURCE = "35.235.240.0/20"
GCP_IAP_PRIORITY = 850
GCP_FILTER_ALLOW_PRIORITY = 875
GCP_FILTER_DENY_PRIORITY = 1000

IAP_RULE = "bioaf-ssh-recovery-iap"
ALLOW_RULES = {4: "bioaf-ssh-filter-allow-v4", 6: "bioaf-ssh-filter-allow-v6"}
DENY_RULES = {4: "bioaf-ssh-filter-deny-v4", 6: "bioaf-ssh-filter-deny-v6"}
EVERYWHERE = {4: "0.0.0.0/0", 6: "::/0"}
AWS_RECOVERY_GROUP = "bioaf-ssh-recovery"
# The notebook gateway (plan 03.5A) is reachable from exactly where the application's HTTPS is.
# bioAF owns only the 8443 access it creates: on GCP the rules named ``GATEWAY_RULE`` or
# ``GATEWAY_RULE-*``, on AWS the 8443 sources carrying ``AWS_GATEWAY_MARK`` as their description.
GATEWAY_PORT = 8443
HTTPS_PORT = 443
GATEWAY_RULE = "bioaf-notebook-gateway"
AWS_GATEWAY_MARK = "bioAF notebook gateway"
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


def _reaches(port: int, protocol: str, ports: list[str]) -> bool:
    if str(protocol) not in ("tcp", "6", "all"):
        return False
    if not ports:
        return True
    for spec in ports:
        low, _, high = str(spec).partition("-")
        if int(low) <= port <= int(high or low):
            return True
    return False


def _reaches_ssh(protocol: str, ports: list[str]) -> bool:
    return _reaches(22, protocol, ports)


def _aws_reaches(permission: dict, port: int) -> bool:
    protocol = str(permission.get("IpProtocol"))
    if protocol == "-1":
        return True
    if protocol not in ("tcp", "6"):
        return False
    return int(permission.get("FromPort", 0)) <= port <= int(permission.get("ToPort", 65535))


@dataclass(frozen=True)
class HostTargets:
    """This host's own network tags and service accounts, as its metadata server reports them."""

    tags: tuple[str, ...] = ()
    service_accounts: tuple[str, ...] = ()


def gateway_rule_name(source_rule: str) -> str:
    """The name of bioAF's GCP rule mirroring ``source_rule`` onto the gateway port (fits GCP's 63)."""
    return f"{GATEWAY_RULE}-{hashlib.sha256(source_rule.encode()).hexdigest()[:12]}"


def _is_gateway_rule(name: str) -> bool:
    return name == GATEWAY_RULE or name.startswith(GATEWAY_RULE + "-")


# AWS permission sources and the fields that identify each one.
_AWS_SOURCE_FIELDS = {
    "IpRanges": ("CidrIp",),
    "Ipv6Ranges": ("CidrIpv6",),
    "PrefixListIds": ("PrefixListId",),
    "UserIdGroupPairs": ("GroupId", "UserId", "VpcPeeringConnectionId"),
}


def _aws_sources(permission: dict):
    """(identity, kind, entry) for each source of an AWS permission."""
    for kind, fields in _AWS_SOURCE_FIELDS.items():
        for entry in permission.get(kind) or []:
            yield (kind, *(entry.get(f) for f in fields)), kind, entry


def _aws_gateway_permission(sources: list[tuple[str, dict]]) -> dict:
    permission: dict = {"IpProtocol": "tcp", "FromPort": GATEWAY_PORT, "ToPort": GATEWAY_PORT}
    for kind, entry in sources:
        permission.setdefault(kind, []).append(entry)
    return permission


# Rule lists a patch cannot empty (an empty list is not sent) and GCP refuses a full update, so
# removing one rebuilds the rule (see GcpHostNetwork._upsert).
_CLEARED_BY_UPDATE_ONLY = (
    "source_tags",
    "source_service_accounts",
    "target_service_accounts",
    "destination_ranges",
    "source_ranges",
    "target_tags",
    "allowed",
    "denied",
)


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
            disabled=False,
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

    @staticmethod
    def _network_path(network: str) -> str:
        """projects/<p>/global/networks/<n>, whether the API returned a full URL or the short form."""
        index = (network or "").find("projects/")
        return network[index:] if index >= 0 else network or ""

    def _effect(self, rule) -> tuple:
        """Everything that decides what a rule lets through (or blocks), for comparison."""
        return (
            rule.priority,
            sorted(rule.source_ranges),
            sorted(rule.target_tags),
            sorted((a.I_p_protocol, tuple(sorted(a.ports))) for a in rule.allowed),
            sorted((d.I_p_protocol, tuple(sorted(d.ports))) for d in rule.denied),
            bool(rule.disabled),
            rule.direction or "INGRESS",
            self._network_path(rule.network),
            # GCP admits a source tag or service account OR a source range, so extra selectors widen
            # access; target service accounts and destination ranges change what the rule covers.
            sorted(rule.source_tags),
            sorted(rule.source_service_accounts),
            sorted(rule.target_service_accounts),
            sorted(rule.destination_ranges),
        )

    def _upsert(self, rule) -> None:
        """Create or repair ``rule`` so its effect is exactly the intended one.

        A patch repairs fields it can set. A list it would have to empty (an added source tag,
        service account or range) cannot be patched away and GCP refuses a full update, so the rule
        is rebuilt make-before-break: a correct twin first, then the rule is replaced, then the twin
        goes, so the intended access is never missing. A twin an interrupted rebuild left is removed.
        """
        twin_name = f"{rule.name}-repair"
        current = self._existing(rule.name)
        if current is None:
            self.client.insert(project=self.project, firewall_resource=rule).result(timeout=120)
        elif self._effect(current) != self._effect(rule):
            if any(list(getattr(current, f)) and not list(getattr(rule, f)) for f in _CLEARED_BY_UPDATE_ONLY):
                twin = type(rule)(rule)
                twin.name = twin_name
                if self._existing(twin_name) is None:
                    self.client.insert(project=self.project, firewall_resource=twin).result(timeout=120)
                self.client.delete(project=self.project, firewall=rule.name).result(timeout=120)
                self.client.insert(project=self.project, firewall_resource=rule).result(timeout=120)
            else:
                self.client.patch(project=self.project, firewall=rule.name, firewall_resource=rule).result(timeout=120)
        if self._existing(twin_name) is not None:
            self.client.delete(project=self.project, firewall=twin_name).result(timeout=120)

    def _delete(self, name) -> None:
        if self._existing(name) is not None:
            self.client.delete(project=self.project, firewall=name).result(timeout=120)

    def _applies_here(self, rule, targets: HostTargets | None, *, restricts: bool) -> bool:
        """Whether ``rule`` applies to this host. Without this host's service accounts, a rule that
        targets accounts is taken to apply when it restricts access and not when it grants it."""
        tags = set(rule.target_tags or [])
        accounts = set(rule.target_service_accounts or [])
        if not tags and not accounts:
            return True
        if tags & {self.target_tag, *(targets.tags if targets else ())}:
            return True
        if accounts:
            return restricts if targets is None else bool(accounts & set(targets.service_accounts))
        return False

    def ensure_gateway_port(self, targets: HostTargets | None = None) -> dict:
        """Make the notebook gateway port reachable from exactly where HTTPS is on this host.

        Each enabled ingress rule that allows or denies tcp:443 on this host, and does not already
        cover the gateway port, gets a bioAF rule matching only tcp:8443 with the same action,
        priority, sources and destinations, targeting this host. GCP decides each port by its own
        highest-priority matching rule, so 8443 then behaves as 443 does, exceptions included, and
        no rule matching 443 changes. Rules bioAF does not own are never modified. Mirrors of rules
        that changed are updated, and mirrors of rules that are gone are removed: restrictions are
        written before the access they restrict and removed after it.
        """
        from google.cloud import compute_v1

        rules = list(self.client.list(project=self.project))
        wanted = {}
        for rule in rules:
            if _is_gateway_rule(rule.name) or self._network_path(rule.network) != self.network:
                continue
            if getattr(rule, "disabled", False) or (rule.direction or "INGRESS") != "INGRESS":
                continue
            restricts = bool(rule.denied)
            ports = [(p.I_p_protocol, list(p.ports or [])) for p in (rule.denied if restricts else rule.allowed)]
            if not any(_reaches(HTTPS_PORT, proto, p) for proto, p in ports):
                continue
            if any(_reaches(GATEWAY_PORT, proto, p) for proto, p in ports):
                continue
            if not self._applies_here(rule, targets, restricts=restricts):
                continue
            gateway = [str(GATEWAY_PORT)]
            mirror = compute_v1.Firewall(
                name=gateway_rule_name(rule.name),
                network=self.network,
                disabled=False,
                direction="INGRESS",
                priority=rule.priority,
                source_ranges=list(rule.source_ranges or []),
                source_tags=list(rule.source_tags or []),
                source_service_accounts=list(rule.source_service_accounts or []),
                destination_ranges=list(rule.destination_ranges or []),
                target_tags=[self.target_tag],
                description=f"Managed by bioAF: {rule.name} applied to the notebook gateway (tcp:{GATEWAY_PORT}).",
            )
            if restricts:
                mirror.denied = [compute_v1.Denied(I_p_protocol="tcp", ports=gateway)]
            else:
                mirror.allowed = [compute_v1.Allowed(I_p_protocol="tcp", ports=gateway)]
            wanted[mirror.name] = mirror
        for mirror in sorted(wanted.values(), key=lambda m: (not m.denied, m.name)):
            self._upsert(mirror)
        stale = [r for r in rules if _is_gateway_rule(r.name) and r.name not in wanted]
        for rule in sorted(stale, key=lambda r: (bool(r.denied), r.name)):
            self._delete(rule.name)
        sources = sorted({s for m in wanted.values() if m.allowed for s in m.source_ranges})
        return {"opened": any(m.allowed for m in wanted.values()), "sources": sources}

    def recovery_sources(self) -> list[dict]:
        return [{"kind": "gcp_iap", "source": GCP_IAP_SOURCE, "priority": GCP_IAP_PRIORITY, "ports": "tcp:22"}]

    def ensure_recovery(self) -> list[dict]:
        self._upsert(self._rule(IAP_RULE, priority=GCP_IAP_PRIORITY, sources=[GCP_IAP_SOURCE], allow=True))
        return self.recovery_sources()

    def recovery_missing(self) -> bool:
        """True when bioAF's recovery rule was never created here (an install from before it existed)."""
        return self._existing(IAP_RULE) is None

    def check_recovery(self) -> list[str]:
        """Problems with recovery that exists; a rule never set up is recovery_missing, not a problem."""
        problems = []
        iap = self._existing(IAP_RULE)
        expected = self._rule(IAP_RULE, priority=GCP_IAP_PRIORITY, sources=[GCP_IAP_SOURCE], allow=True)
        if iap is not None and self._effect(iap) != self._effect(expected):
            problems.append("The IAP recovery rule was changed outside bioAF.")
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

    def recovery_missing(self) -> bool:
        """True when bioAF's recovery security group was never created in this VPC."""
        return self._find_group(AWS_RECOVERY_GROUP, self._instance()["VpcId"]) is None

    def check_recovery(self) -> list[str]:
        """Problems with recovery that exists; a group never set up is recovery_missing, not a problem."""
        problems = []
        instance = self._instance()
        attached = [g["GroupId"] for g in instance["SecurityGroups"]]
        recovery = self._find_group(AWS_RECOVERY_GROUP, instance["VpcId"])
        if recovery is not None and recovery not in attached:
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

    def ensure_gateway_port(self, targets: HostTargets | None = None) -> dict:
        """Make the notebook gateway port reachable from exactly the sources HTTPS has on this
        instance's groups. Security groups only allow, so each group gets, for 8443, the sources
        its HTTPS permissions have and that do not already reach 8443, marked as bioAF's; bioAF's
        marked 8443 sources that HTTPS no longer has are revoked after anything new is added.
        Permissions bioAF did not add, and every HTTPS permission, are never changed."""
        group_ids = [g["GroupId"] for g in self._instance()["SecurityGroups"]]
        groups = self.client.describe_security_groups(GroupIds=group_ids)["SecurityGroups"]
        additions, removals = [], []
        for group in groups:
            permissions = group.get("IpPermissions", [])
            wanted: dict = {}
            for permission in permissions:
                if _aws_reaches(permission, HTTPS_PORT) and not _aws_reaches(permission, GATEWAY_PORT):
                    for key, kind, entry in _aws_sources(permission):
                        fields = _AWS_SOURCE_FIELDS[kind]
                        wanted[key] = (
                            kind,
                            {**{f: entry[f] for f in fields if entry.get(f)}, "Description": AWS_GATEWAY_MARK},
                        )
            reaching, owned = set(), {}
            for permission in permissions:
                if not _aws_reaches(permission, GATEWAY_PORT):
                    continue
                exact = str(permission.get("IpProtocol")) in ("tcp", "6") and (
                    permission.get("FromPort"),
                    permission.get("ToPort"),
                ) == (GATEWAY_PORT, GATEWAY_PORT)
                for key, kind, entry in _aws_sources(permission):
                    reaching.add(key)
                    if exact and entry.get("Description") == AWS_GATEWAY_MARK:
                        owned[key] = (kind, entry)
            add = [source for key, source in wanted.items() if key not in reaching]
            remove = [source for key, source in owned.items() if key not in wanted]
            if add:
                additions.append((group["GroupId"], add))
            if remove:
                removals.append((group["GroupId"], remove))
        for group_id, sources in additions:
            try:
                self.client.authorize_security_group_ingress(
                    GroupId=group_id, IpPermissions=[_aws_gateway_permission(sources)]
                )
            except Exception as exc:
                if "Duplicate" not in str(exc):
                    raise
                for source in sources:  # one already exists: add the rest one by one
                    self._authorize(GroupId=group_id, IpPermissions=[_aws_gateway_permission([source])])
        for group_id, sources in removals:
            self.client.revoke_security_group_ingress(
                GroupId=group_id, IpPermissions=[_aws_gateway_permission(sources)]
            )
        return {"opened": bool(additions), "sources": []}

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


def gcp_recovery_command(instance_name: str, project: str, zone: str) -> str:
    """The Cloud Shell command for recovery SSH to a public GCP server, through IAP."""
    return f"gcloud compute ssh {instance_name} --project={project} --zone={zone} --tunnel-through-iap"


def host_network(identity, gcp_config: dict | None = None):
    """The provider network driver for this host, with the backend's configured authority."""
    if identity.provider == "gcp":
        from google.cloud import compute_v1

        from app.adapters.credentials.credential_injector import load_gcp_credentials

        client = compute_v1.FirewallsClient(credentials=load_gcp_credentials(gcp_config or {}))
        return GcpHostNetwork(identity, client=client, target_tag="bioaf")
    import boto3

    return AwsHostNetwork(identity, client=boto3.client("ec2", region_name=identity.region))


def find_eic_endpoint(region: str, instance_id: str) -> str:
    """The prepared EC2 Instance Connect Endpoint in a private server's VPC, or "" (best effort).

    The recovery rule admits the endpoint's security group, which is the source only when the
    endpoint does not preserve client addresses, so such an endpoint is preferred. Blocking: call
    it from a worker thread.
    """
    try:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        instance = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
        endpoints = ec2.describe_instance_connect_endpoints(
            Filters=[{"Name": "vpc-id", "Values": [instance["VpcId"]]}]
        ).get("InstanceConnectEndpoints", [])
    except Exception:
        return ""
    ready = [e for e in endpoints if e.get("State") in (None, "create-complete")]
    ready.sort(key=lambda e: bool(e.get("PreserveClientIp")))
    return ready[0].get("InstanceConnectEndpointId", "") if ready else ""


def aws_app_network(region: str, instance_id: str) -> tuple[str, str] | None:
    """(VPC id, application security group id) of the running instance, or None. bioAF's own SSH
    groups are never the application group. Blocking: call it from a worker thread."""
    import boto3

    ec2 = boto3.client("ec2", region_name=region)
    instance = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    groups = [g for g in instance.get("SecurityGroups", []) if not g.get("GroupName", "").startswith("bioaf-ssh-")]
    if not groups:
        return None
    return instance["VpcId"], groups[0]["GroupId"]
