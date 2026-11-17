"""The public webhook event contract: exact ``data`` fields for each of the eight events.

Producers build their bus payload here, from snapshots taken at mutation time
(app/services/entity_references.py), and hand it to ``defer_event`` on the session that made the
change. The bus payload is ``{"organization_id": <int>, "data": {...}}``; the experiment status
event additionally carries the legacy notification fields its other subscribers read.

The dispatcher (app/services/webhook_dispatcher.py) validates ``data`` against ``EVENT_FIELDS``
before persisting a delivery, so a producer cannot leak an extra field (notes, custom-field values,
URLs) into a receiver's body.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from app.services.event_types import (
    WEBHOOK_EXPERIMENT_CREATED,
    WEBHOOK_EXPERIMENT_STATUS_CHANGED,
    WEBHOOK_EXPERIMENT_UPDATED,
    WEBHOOK_FILE_READY,
    WEBHOOK_FILE_REGISTERED,
    WEBHOOK_SAMPLE_CREATED,
    WEBHOOK_SAMPLE_QC_CHANGED,
    WEBHOOK_SAMPLE_UPDATED,
)

_EXPERIMENT = ("experiment_id", "external_id", "project_id", "project_external_id")
_SAMPLE = ("sample_id", "external_id", "experiment_id", "experiment_external_id")
_FILE_RELATIONS = (
    "project_id",
    "project_external_id",
    "experiment_id",
    "experiment_external_id",
    "sample_ids",
    "samples",
)

EVENT_FIELDS: dict[str, frozenset[str]] = {
    WEBHOOK_EXPERIMENT_CREATED: frozenset(_EXPERIMENT),
    WEBHOOK_EXPERIMENT_UPDATED: frozenset((*_EXPERIMENT, "changed_fields")),
    WEBHOOK_EXPERIMENT_STATUS_CHANGED: frozenset((*_EXPERIMENT, "old_status", "new_status")),
    WEBHOOK_SAMPLE_CREATED: frozenset(_SAMPLE),
    WEBHOOK_SAMPLE_UPDATED: frozenset((*_SAMPLE, "changed_fields")),
    WEBHOOK_SAMPLE_QC_CHANGED: frozenset((*_SAMPLE, "old_qc_status", "new_qc_status")),
    WEBHOOK_FILE_REGISTERED: frozenset(
        ("file_id", "external_id", "filename", "file_type", "source_type", *_FILE_RELATIONS)
    ),
    WEBHOOK_FILE_READY: frozenset(("file_id", "external_id", "ready_at", "size_bytes", *_FILE_RELATIONS)),
}

SAMPLE_REFERENCE_FIELDS = frozenset(_SAMPLE)


class InvalidEventData(ValueError):
    """``data`` does not have exactly the fields of its public event."""


def validate_data(public_event: str, data: Any) -> None:
    expected = EVENT_FIELDS.get(public_event)
    if expected is None:
        raise InvalidEventData(f"no data contract for {public_event}")
    if not isinstance(data, dict) or set(data) != expected:
        raise InvalidEventData(f"{public_event} data does not match its contract")
    if public_event in (WEBHOOK_FILE_REGISTERED, WEBHOOK_FILE_READY):
        samples = data["samples"]
        if not isinstance(samples, list) or any(
            not isinstance(s, dict) or set(s) != SAMPLE_REFERENCE_FIELDS for s in samples
        ):
            raise InvalidEventData(f"{public_event} samples do not match their contract")
        if [s["sample_id"] for s in samples] != data["sample_ids"]:
            raise InvalidEventData(f"{public_event} samples and sample_ids disagree")


def changed(fields: Iterable[str]) -> list[str]:
    """``changed_fields``: the sorted unique canonical names of what actually changed."""
    return sorted(set(fields))


def _bus(org_id: int, data: dict[str, Any]) -> dict[str, Any]:
    return {"organization_id": org_id, "data": data}


def experiment_created(org_id: int, ref: dict[str, Any]) -> dict[str, Any]:
    return _bus(org_id, {k: ref[k] for k in _EXPERIMENT})


def experiment_updated(org_id: int, ref: dict[str, Any], changed_fields: Iterable[str]) -> dict[str, Any]:
    return _bus(org_id, {**{k: ref[k] for k in _EXPERIMENT}, "changed_fields": changed(changed_fields)})


def experiment_status_data(ref: dict[str, Any], old_status: str | None, new_status: str) -> dict[str, Any]:
    return {**{k: ref[k] for k in _EXPERIMENT}, "old_status": old_status, "new_status": new_status}


def sample_created(org_id: int, ref: dict[str, Any]) -> dict[str, Any]:
    return _bus(org_id, {k: ref[k] for k in _SAMPLE})


def sample_updated(org_id: int, ref: dict[str, Any], changed_fields: Iterable[str]) -> dict[str, Any]:
    return _bus(org_id, {**{k: ref[k] for k in _SAMPLE}, "changed_fields": changed(changed_fields)})


def sample_qc_changed(org_id: int, ref: dict[str, Any], old: str | None, new: str | None) -> dict[str, Any]:
    return _bus(org_id, {**{k: ref[k] for k in _SAMPLE}, "old_qc_status": old, "new_qc_status": new})


def _relations(rel: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": rel["project_id"],
        "project_external_id": rel["project_external_id"],
        "experiment_id": rel["experiment_id"],
        "experiment_external_id": rel["experiment_external_id"],
        "sample_ids": list(rel["sample_ids"]),
        "samples": [dict(s) for s in rel["samples"]],
    }


def file_registered(file, rel: dict[str, Any]) -> dict[str, Any]:
    return _bus(
        file.organization_id,
        {
            "file_id": file.id,
            "external_id": file.external_id,
            "filename": file.filename,
            "file_type": file.file_type,
            "source_type": file.source_type,
            **_relations(rel),
        },
    )


def utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def file_ready(file, rel: dict[str, Any]) -> dict[str, Any]:
    return _bus(
        file.organization_id,
        {
            "file_id": file.id,
            "external_id": file.external_id,
            "ready_at": utc_iso(file.ready_at),
            "size_bytes": file.size_bytes,
            **_relations(rel),
        },
    )
