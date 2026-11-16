"""The closed allowlist of an installation metrics report (ADR-075).

A report is built only from typed fields and checked again before it is sent. Unknown keys and
invalid values are refused; the error names the field, never the value. Nothing else (request
context, models, exceptions, generic event payloads) is ever serialized into a report.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone

METRICS_SCHEMA_VERSION = 1
UNKNOWN_VERSION = "unknown"

INSTALLATION_COMPLETED = "bioaf_installation_completed"
INSTALLATION_OBSERVED = "bioaf_installation_observed"
UPGRADE_COMPLETED = "bioaf_upgrade_completed"

# How an event describes the installation if it is the first one PostHog sees for it.
REGISTRATION_KIND = {
    INSTALLATION_COMPLETED: "new",
    INSTALLATION_OBSERVED: "existing",
    UPGRADE_COMPLETED: "first_seen_on_upgrade",
}

# Official releases are calendar versions (YYYY.M.N), as tagged by the release workflow.
_OFFICIAL = re.compile(r"^v?(\d{4})\.(\d{1,2})\.(\d{1,4})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_ENVELOPE_KEYS = {"event", "uuid", "distinct_id", "timestamp", "properties"}
_PROPERTY_KEYS = {"metrics_schema_version", "bioaf_version", "$geoip_disable", "$set_once"}
_SET_ONCE_KEYS = {"first_reported_date", "registration_kind"}


class PayloadRejected(ValueError):
    """A report field outside the allowlist or with an invalid value. The message names only the field."""


def official_version(raw: object) -> str:
    """``raw`` as an official release version, or ``unknown`` for anything else (custom or branch builds)."""
    if not isinstance(raw, str):
        return UNKNOWN_VERSION
    match = _OFFICIAL.match(raw.strip())
    if match is None or not 1 <= int(match.group(2)) <= 12:
        return UNKNOWN_VERSION
    return f"{int(match.group(1))}.{int(match.group(2))}.{int(match.group(3))}"


def report_day(moment: datetime) -> date:
    """The UTC calendar day of ``moment``: the only time a report carries."""
    if moment.tzinfo is None:
        raise PayloadRejected("timestamp")
    return moment.astimezone(timezone.utc).date()


def build_event(
    *,
    name: str,
    event_uuid: str,
    installation_id: str,
    day: date,
    bioaf_version: object,
    from_version: object = None,
) -> dict:
    """A report for ``name``, every field typed and checked."""
    properties: dict = {
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
        "bioaf_version": official_version(bioaf_version),
        "$geoip_disable": True,
        "$set_once": {"first_reported_date": day.isoformat(), "registration_kind": REGISTRATION_KIND.get(name)},
    }
    if name == UPGRADE_COMPLETED:
        properties["from_version"] = official_version(from_version)
    event = {
        "event": name,
        "uuid": event_uuid,
        "distinct_id": installation_id,
        "timestamp": f"{day.isoformat()}T00:00:00Z",
        "properties": properties,
    }
    return validate_event(event)


def _uuid(value: object, field: str) -> None:
    try:
        uuid.UUID(str(value))
    except ValueError:
        raise PayloadRejected(field) from None
    if not isinstance(value, str):
        raise PayloadRejected(field)


def _version(value: object, field: str) -> None:
    if not isinstance(value, str) or (value != UNKNOWN_VERSION and official_version(value) != value):
        raise PayloadRejected(field)


def validate_event(event: object) -> dict:
    """``event`` unchanged if it is exactly an allowed report; PayloadRejected otherwise."""
    if not isinstance(event, dict) or set(event) != _ENVELOPE_KEYS:
        raise PayloadRejected("envelope")
    name = event["event"]
    if name not in REGISTRATION_KIND:
        raise PayloadRejected("event")
    _uuid(event["uuid"], "uuid")
    _uuid(event["distinct_id"], "distinct_id")
    timestamp = event["timestamp"]
    if not isinstance(timestamp, str) or not timestamp.endswith("T00:00:00Z") or not _DAY.match(timestamp[:10]):
        raise PayloadRejected("timestamp")
    try:
        date.fromisoformat(timestamp[:10])
    except ValueError:
        raise PayloadRejected("timestamp") from None

    properties = event["properties"]
    allowed = _PROPERTY_KEYS | ({"from_version"} if name == UPGRADE_COMPLETED else set())
    if not isinstance(properties, dict) or set(properties) != allowed:
        raise PayloadRejected("properties")
    if properties["metrics_schema_version"] != METRICS_SCHEMA_VERSION or isinstance(
        properties["metrics_schema_version"], bool
    ):
        raise PayloadRejected("metrics_schema_version")
    _version(properties["bioaf_version"], "bioaf_version")
    if name == UPGRADE_COMPLETED:
        _version(properties["from_version"], "from_version")
    if properties["$geoip_disable"] is not True:
        raise PayloadRejected("$geoip_disable")

    set_once = properties["$set_once"]
    if not isinstance(set_once, dict) or set(set_once) != _SET_ONCE_KEYS:
        raise PayloadRejected("$set_once")
    first = set_once["first_reported_date"]
    if not isinstance(first, str) or first != timestamp[:10]:
        raise PayloadRejected("first_reported_date")
    if set_once["registration_kind"] != REGISTRATION_KIND[name]:
        raise PayloadRejected("registration_kind")
    return event
