"""SSH access policy for Work Nodes and the application host (plan 03B).

Work Node ranges (D03-5, network OR key): inside a configured source range a scientist may log in
with password or key; from anywhere else only with their public key. ``bioaf-sync`` is key-only
everywhere. The policy is enforced by sshd on each node at launch; it does not change port 22
reachability, so public Work Nodes keep their R05 exposure. Running nodes keep the policy they were
launched with; a change applies to Work Nodes launched after it.

The record lives in platform configuration (``work_node_ssh_policy``): a draft (Save changes,
versioned for stale-edit protection), the applied policy (Apply changes, a new revision each time)
and the previously applied policy (rollback). Ranges have stable ids and no expiry. A record this
release cannot read stops launches instead of being treated as "no range".
"""

from __future__ import annotations

import ipaddress
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.exceptions import ConflictError, ValidationError

WORK_NODE_POLICY_KEY = "work_node_ssh_policy"
MAX_RANGES = 50
MAX_NAME = 80


@dataclass(frozen=True)
class WorkNodeSshPolicy:
    revision: int = 0
    ranges: tuple[str, ...] = field(default_factory=tuple)

    def as_spec(self) -> dict:
        return {"revision": self.revision, "ranges": list(self.ranges)}


def _empty_record() -> dict:
    return {
        "version": 1,
        "draft": {"version": 0, "enabled": False, "ranges": []},
        "applied": {"revision": 0, "enabled": False, "ranges": []},
        "previous": None,
    }


async def _read(session) -> dict:
    from app.platform.platform_config_service import PlatformConfigService

    raw = await PlatformConfigService.get(session, WORK_NODE_POLICY_KEY)
    if not raw:
        return _empty_record()
    try:
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get("version") != 1:
            raise ValueError
        return record
    except (ValueError, TypeError):
        raise ValidationError(
            "The work node SSH access setting could not be read. Ask an administrator to review it."
        ) from None


async def _write(session, record: dict) -> None:
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, WORK_NODE_POLICY_KEY, json.dumps(record))


def normalize_range(text: str) -> str:
    """One canonical network; a single address becomes /32 or /128. Universal ranges are refused."""
    try:
        network = ipaddress.ip_network(str(text).strip(), strict=True)
    except ValueError:
        raise ValidationError(
            f"'{str(text)[:60]}' is not an IP address or a network range such as 10.20.0.0/16."
        ) from None
    if network.prefixlen == 0:
        raise ValidationError("A range that includes every address is not a restriction. Enter a specific range.")
    return str(network)


def _validated_entries(entries: list[dict], existing: list[dict]) -> list[dict]:
    if len(entries) > MAX_RANGES:
        raise ValidationError(f"Enter at most {MAX_RANGES} ranges.")
    known = {e["cidr"]: e["id"] for e in existing}
    seen = set()
    result = []
    for entry in entries:
        name = str(entry.get("name") or "").strip()[:MAX_NAME]
        cidr = normalize_range(entry.get("cidr", ""))
        if cidr in seen:
            continue
        seen.add(cidr)
        result.append(
            {"id": entry.get("id") or known.get(cidr) or uuid.uuid4().hex, "name": name or cidr, "cidr": cidr}
        )
    return result


def _enabled_ranges(section: dict) -> tuple[str, ...]:
    if not section.get("enabled"):
        return ()
    return tuple(normalize_range(r["cidr"]) for r in section.get("ranges", []))


async def applied_work_node_policy(session) -> WorkNodeSshPolicy:
    """The policy future launches apply; no record means no range (today's password/key behavior)."""
    record = await _read(session)
    applied = record.get("applied") or {}
    try:
        return WorkNodeSshPolicy(revision=int(applied.get("revision", 0)), ranges=_enabled_ranges(applied))
    except (ValidationError, TypeError, KeyError):
        raise ValidationError(
            "The work node SSH access setting could not be read. Ask an administrator to review it."
        ) from None


async def work_node_policy_view(session) -> dict:
    record = await _read(session)
    applied = record["applied"]
    return {
        "draft": record["draft"],
        "applied": {"revision": applied["revision"], "enabled": applied["enabled"], "ranges": applied["ranges"]},
        "applied_at": applied.get("applied_at"),
        "can_roll_back": record.get("previous") is not None,
    }


async def save_work_node_draft(session, *, enabled: bool, ranges: list[dict], base_version: int, actor_id: int) -> dict:
    from app.services.audit_service import log_action

    record = await _read(session)
    draft = record["draft"]
    if base_version != draft["version"]:
        raise ConflictError("These settings were changed by someone else. Reload them, then make your change again.")
    validated = _validated_entries(ranges, draft.get("ranges", []) + record["applied"].get("ranges", []))
    if enabled and not validated:
        raise ValidationError("Add at least one range, or turn the restriction off.")
    record["draft"] = {
        "version": draft["version"] + 1,
        "enabled": bool(enabled),
        "ranges": validated,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "saved_by": actor_id,
    }
    await _write(session, record)
    await log_action(
        session,
        user_id=actor_id,
        entity_type="ssh_access_policy",
        entity_id=0,
        action="save_work_node_ssh_policy_draft",
        details={"enabled": bool(enabled), "ranges": [r["cidr"] for r in validated]},
    )
    return record["draft"]


def describe_work_node_policy(enabled: bool, ranges: list[str]) -> list[str]:
    if not enabled or not ranges:
        return [
            "No range is set: scientists can log in to their work nodes with a password or their key from anywhere.",
            "Work nodes with a public address can still be reached by SSH from the internet.",
        ]
    return [
        f"From inside {', '.join(ranges)}: password or key. From anywhere else: key only.",
        "The backend's own sync account always needs its key.",
        "This applies to work nodes launched after you apply it; running work nodes keep their current rule.",
        "This does not limit which networks can reach SSH: work nodes with a public address are still reachable.",
    ]


async def check_work_node_draft(session) -> dict:
    record = await _read(session)
    draft = record["draft"]
    try:
        ranges = [normalize_range(r["cidr"]) for r in draft.get("ranges", [])]
    except ValidationError as exc:
        return {"valid": False, "messages": [str(exc)]}
    if draft.get("enabled") and not ranges:
        return {"valid": False, "messages": ["Add at least one range, or turn the restriction off."]}
    return {"valid": True, "messages": describe_work_node_policy(bool(draft.get("enabled")), ranges)}


async def apply_work_node_draft(session, *, draft_version: int, actor_id: int) -> dict:
    from app.services.audit_service import log_action

    record = await _read(session)
    draft = record["draft"]
    if draft_version != draft["version"]:
        raise ConflictError("These settings were changed by someone else. Reload them, then apply again.")
    check = await check_work_node_draft(session)
    if not check["valid"]:
        raise ValidationError(" ".join(check["messages"]))
    applied = record["applied"]
    record["previous"] = applied
    record["applied"] = {
        "revision": applied["revision"] + 1,
        "enabled": bool(draft.get("enabled")),
        "ranges": draft.get("ranges", []),
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "applied_by": actor_id,
    }
    await _write(session, record)
    await log_action(
        session,
        user_id=actor_id,
        entity_type="ssh_access_policy",
        entity_id=0,
        action="apply_work_node_ssh_policy",
        details={
            "revision": record["applied"]["revision"],
            "enabled": record["applied"]["enabled"],
            "ranges": [r["cidr"] for r in record["applied"]["ranges"]],
        },
        previous_value={"revision": applied["revision"], "ranges": [r["cidr"] for r in applied.get("ranges", [])]},
    )
    return record["applied"]


async def rollback_work_node_policy(session, *, actor_id: int) -> dict:
    from app.services.audit_service import log_action

    record = await _read(session)
    previous = record.get("previous")
    if not previous:
        raise ValidationError("There is no earlier applied setting to return to.")
    current = record["applied"]
    record["applied"] = {
        **previous,
        "revision": current["revision"] + 1,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "applied_by": actor_id,
    }
    record["previous"] = current
    await _write(session, record)
    await log_action(
        session,
        user_id=actor_id,
        entity_type="ssh_access_policy",
        entity_id=0,
        action="rollback_work_node_ssh_policy",
        details={"revision": record["applied"]["revision"], "ranges": [r["cidr"] for r in record["applied"]["ranges"]]},
    )
    return record["applied"]
