"""SSH access policy for Work Nodes and the application host (plan 03B).

Work Node ranges (D03-5, network OR key): inside a configured source range a scientist may log in
with password or key; from anywhere else only with their public key. ``bioaf-sync`` is key-only
everywhere. The policy is enforced by sshd on each node at launch; it does not change port 22
reachability, so public Work Nodes keep their R05 exposure. Running nodes keep the policy they were
launched with.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field

WORK_NODE_POLICY_KEY = "work_node_ssh_policy"


@dataclass(frozen=True)
class WorkNodeSshPolicy:
    revision: int = 0
    ranges: tuple[str, ...] = field(default_factory=tuple)

    def as_spec(self) -> dict:
        return {"revision": self.revision, "ranges": list(self.ranges)}


async def applied_work_node_policy(session) -> WorkNodeSshPolicy:
    """The applied Work Node policy; no record means no range (today's password/key behavior)."""
    from app.platform.platform_config_service import PlatformConfigService

    raw = await PlatformConfigService.get(session, WORK_NODE_POLICY_KEY)
    if not raw:
        return WorkNodeSshPolicy()
    try:
        record = json.loads(raw)
        applied = record.get("applied") or {}
        ranges = tuple(str(ipaddress.ip_network(r["cidr"], strict=True)) for r in applied.get("ranges", []))
        return WorkNodeSshPolicy(revision=int(applied.get("revision", 0)), ranges=ranges)
    except (ValueError, TypeError, KeyError):
        # A record this release cannot read is never treated as "no range": launches stop instead.
        from app.exceptions import ValidationError

        raise ValidationError(
            "The work node SSH access setting could not be read. Ask an administrator to review it."
        ) from None
