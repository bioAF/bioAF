"""The closed allowlist of an installation metrics report (ADR-075)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.install_metrics.payload import (
    INSTALLATION_COMPLETED,
    INSTALLATION_OBSERVED,
    UPGRADE_COMPLETED,
    PayloadRejected,
    build_event,
    official_version,
    report_day,
    validate_event,
)

IID = str(uuid.uuid4())
EID = str(uuid.uuid4())
DAY = date(2026, 11, 20)


def _event(name=INSTALLATION_COMPLETED, **kwargs):
    return build_event(name=name, event_uuid=EID, installation_id=IID, day=DAY, bioaf_version="2026.11.27", **kwargs)


def test_a_report_carries_exactly_the_approved_fields():
    assert _event() == {
        "event": "bioaf_installation_completed",
        "uuid": EID,
        "distinct_id": IID,
        "timestamp": "2026-11-20T00:00:00Z",
        "properties": {
            "metrics_schema_version": 1,
            "bioaf_version": "2026.11.27",
            "$geoip_disable": True,
            "$set_once": {"first_reported_date": "2026-11-20", "registration_kind": "new"},
        },
    }


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        (INSTALLATION_COMPLETED, "new"),
        (INSTALLATION_OBSERVED, "existing"),
        (UPGRADE_COMPLETED, "first_seen_on_upgrade"),
    ],
)
def test_each_event_names_how_it_would_first_establish_the_profile(name, kind):
    assert _event(name)["properties"]["$set_once"]["registration_kind"] == kind


def test_an_upgrade_also_carries_its_previous_version():
    event = _event(UPGRADE_COMPLETED, from_version="v2026.11.26")

    assert event["properties"]["from_version"] == "2026.11.26"


@pytest.mark.parametrize(
    "raw",
    ["2026.11.27-dirty", "main", "bmills-feature", "dev", "", None, "2026.13.1", "1.2", "2026.11.27+lab", 42],
)
def test_unofficial_versions_are_reported_as_unknown(raw):
    assert official_version(raw) == "unknown"
    assert _event(UPGRADE_COMPLETED, from_version=raw)["properties"]["from_version"] == "unknown"


def test_the_report_day_is_the_utc_day_of_the_event():
    late_pacific = datetime(2026, 11, 20, 23, 30, tzinfo=timezone(timedelta(hours=-8)))

    assert report_day(late_pacific) == date(2026, 11, 21)
    with pytest.raises(PayloadRejected):
        report_day(datetime(2026, 11, 20, 12, 0))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(extra="x"),
        lambda e: e["properties"].update(email="admin@lab.example"),
        lambda e: e["properties"].update(hostname="lims.lab.example"),
        lambda e: e["properties"].update({"$ip": "203.0.113.9"}),
        lambda e: e["properties"].update(from_version="2026.11.26"),
        lambda e: e["properties"]["$set_once"].update(organization="Lab"),
        lambda e: e["properties"].update({"$geoip_disable": False}),
        lambda e: e["properties"].update(bioaf_version="bmills-branch"),
        lambda e: e.update(timestamp="2026-11-20T15:30:00Z"),
        lambda e: e.update(distinct_id="admin@lab.example"),
        lambda e: e.update(event="bioaf_user_login"),
        lambda e: e["properties"]["$set_once"].update(registration_kind="existing"),
        lambda e: e["properties"]["$set_once"].update(first_reported_date="2020-01-01"),
    ],
)
def test_anything_outside_the_allowlist_is_refused_without_echoing_it(mutate):
    event = _event()
    mutate(event)

    with pytest.raises(PayloadRejected) as raised:
        validate_event(event)

    message = str(raised.value)
    for secret in ("admin@lab.example", "lims.lab.example", "203.0.113.9", "Lab", "bmills-branch"):
        assert secret not in message
