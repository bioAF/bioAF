"""Successful host upgrades as installation metrics reports (ADR-075).

The updater asks the backend to reserve a report after readiness and final host completion
(``app.cli.record_upgrade_metric``); a bounded reconciliation reads the same facts from the shared
update status if that call failed. Both use one source key per host operation and its completion day.
Only an operation that began inside the current uninterrupted enabled interval qualifies.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.database as database
from app.config import settings
from app.models.install_metric_event import InstallMetricEvent
from app.services.install_metrics import outbox, upgrades
from app.services.install_metrics.state import NOTICE_VERSION, InstallMetricsService

ENABLED_AT = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
STARTED = ENABLED_AT + timedelta(days=2)
COMPLETED = STARTED + timedelta(minutes=12)
OP = "8f6b1e0c-2d4a-4c1e-9a77-0b5f3c2d1e00"


@pytest.fixture
def reporting(monkeypatch):
    monkeypatch.setattr(settings, "install_metrics_disabled", False)


def _op(**overrides):
    fields = {
        "operation_id": OP,
        "from_version": "2026.11.26",
        "to_version": "2026.12.1",
        "started_at": STARTED.isoformat(),
        "completed_at": COMPLETED.isoformat(),
        **overrides,
    }
    return upgrades.UpgradeOperation.parse(**fields)


async def _enable(session, at=ENABLED_AT):
    state = await InstallMetricsService.read(session)
    state = await InstallMetricsService.choose(
        session, choice="enabled", notice_version=NOTICE_VERSION, revision=state.revision, now=at
    )
    await session.commit()
    return state


async def _disable(session, at):
    state = await InstallMetricsService.read(session)
    await InstallMetricsService.choose(
        session, choice="disabled", notice_version=NOTICE_VERSION, revision=state.revision, now=at
    )
    await session.commit()


async def _upgrade_reports(session):
    session.expire_all()
    rows = (await session.execute(select(InstallMetricEvent).order_by(InstallMetricEvent.id))).scalars().all()
    return [r for r in rows if r.event_name == "bioaf_upgrade_completed"]


async def _record(session, op=None, now=COMPLETED + timedelta(minutes=1)):
    outcome = await upgrades.record(session, op or _op(), now=now)
    await session.commit()
    return outcome


@pytest.mark.asyncio
async def test_an_upgrade_inside_the_enabled_interval_reserves_one_report_for_its_completion_day(
    session, admin_user, reporting
):
    state = await _enable(session)

    assert await _record(session) == "reserved"

    (report,) = await _upgrade_reports(session)
    assert report.source_key == outbox.upgrade_source(state.installation_id, OP)
    assert report.report_date == COMPLETED.date()
    properties = report.payload["properties"]
    assert properties["bioaf_version"] == "2026.12.1"
    assert properties["from_version"] == "2026.11.26"
    assert properties["$set_once"]["registration_kind"] == "first_seen_on_upgrade"
    assert report.payload["timestamp"] == f"{COMPLETED.date().isoformat()}T00:00:00Z"
    # Nothing beyond the allowlist: no operation ID, no host or database detail.
    assert OP not in json.dumps(report.payload)


@pytest.mark.asyncio
async def test_the_cli_and_reconciliation_record_one_identical_report(
    session, admin_user, reporting, tmp_path, monkeypatch
):
    await _enable(session)
    assert await _record(session) == "reserved"
    (first,) = await _upgrade_reports(session)

    _write_status(tmp_path, status="completed")
    monkeypatch.setattr(settings, "update_status_dir", str(tmp_path))
    assert await upgrades.reconcile_from_status(session, now=COMPLETED + timedelta(hours=1)) == "duplicate"
    await session.commit()

    (again,) = await _upgrade_reports(session)
    assert again.event_uuid == first.event_uuid


@pytest.mark.asyncio
async def test_pending_or_declined_installations_record_no_upgrade(session, admin_user, reporting):
    assert await _record(session) == "not_reporting"
    await _disable(session, ENABLED_AT)
    assert await _record(session) == "not_reporting"

    assert await _upgrade_reports(session) == []
    assert (await InstallMetricsService.read(session)).installation_id is None


@pytest.mark.asyncio
async def test_an_upgrade_that_began_before_consent_is_excluded(session, admin_user, reporting):
    await _enable(session, at=STARTED + timedelta(minutes=5))

    assert await _record(session) == "before_interval"
    assert await _upgrade_reports(session) == []


@pytest.mark.asyncio
async def test_an_upgrade_that_began_while_reporting_was_withdrawn_is_excluded(session, admin_user, reporting):
    await _enable(session)
    await _disable(session, STARTED - timedelta(hours=1))
    await _enable(session, at=STARTED + timedelta(minutes=1))

    assert await _record(session) == "before_interval"
    assert await _upgrade_reports(session) == []


@pytest.mark.asyncio
async def test_the_operator_override_records_nothing(session, admin_user, reporting, monkeypatch):
    await _enable(session)
    monkeypatch.setattr(settings, "install_metrics_disabled", True)

    assert await _record(session) == "not_reporting"
    assert await _upgrade_reports(session) == []


@pytest.mark.asyncio
async def test_a_later_upgrade_does_not_replace_how_the_installation_was_first_seen(session, admin_user, reporting):
    """The baseline and the upgrade both carry $set_once; PostHog keeps the first one it saw."""
    await _enable(session)
    await _record(session)
    session.expire_all()
    rows = (await session.execute(select(InstallMetricEvent).order_by(InstallMetricEvent.id))).scalars().all()

    kinds = [r.payload["properties"]["$set_once"] for r in rows]
    assert [k["registration_kind"] for k in kinds] == ["existing", "first_seen_on_upgrade"]
    assert kinds[0]["first_reported_date"] == ENABLED_AT.date().isoformat()


@pytest.mark.asyncio
async def test_the_pending_upgrade_backlog_is_bounded(session, admin_user, reporting, monkeypatch):
    await _enable(session)
    monkeypatch.setattr(outbox, "MAX_PENDING_UPGRADES", 2)
    outcomes = [await _record(session, _op(operation_id=str(uuid.uuid4()))) for _ in range(3)]

    assert outcomes == ["reserved", "reserved", "backlog_full"]
    assert len(await _upgrade_reports(session)) == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"operation_id": "not-a-uuid"},
        {"operation_id": ""},
        {"started_at": "yesterday"},
        {"completed_at": "2026-11-22T12:00:00"},  # no time zone
        {"completed_at": (STARTED - timedelta(seconds=1)).isoformat()},
        {"to_version": ""},
    ],
)
def test_malformed_operation_facts_are_refused_without_echoing_them(overrides):
    with pytest.raises(upgrades.InvalidOperation) as raised:
        _op(**overrides)

    for value in overrides.values():
        if value:
            assert value not in str(raised.value)


def test_unofficial_versions_are_kept_as_unknown():
    op = _op(from_version="bmills-branch", to_version="2026.12.1-dirty")

    assert (op.from_version, op.to_version) == ("unknown", "unknown")


def _write_status(directory, **fields):
    record = {
        "status": "completed",
        "step": "done",
        "from_version": "2026.11.26",
        "to_version": "2026.12.1",
        "operation_id": OP,
        "operation_started_at": STARTED.isoformat(),
        "operation_completed_at": COMPLETED.isoformat(),
        **fields,
    }
    record = {k: v for k, v in record.items() if v is not None}
    (directory / "current.json").write_text(json.dumps(record))


@pytest.mark.asyncio
async def test_reconciliation_reserves_a_completed_operation_the_cli_missed(
    session, admin_user, reporting, tmp_path, monkeypatch
):
    await _enable(session)
    monkeypatch.setattr(settings, "update_status_dir", str(tmp_path))
    _write_status(tmp_path)

    assert await upgrades.reconcile_from_status(session, now=COMPLETED + timedelta(hours=1)) == "reserved"
    await session.commit()

    (report,) = await _upgrade_reports(session)
    assert report.report_date == COMPLETED.date()


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "in_progress", "operation_completed_at": None},
        {"status": "failed"},
        {"operation_id": None},  # an older updater: no operation identity
        {"operation_completed_at": None},
        {"operation_id": "../../etc/passwd"},
    ],
)
@pytest.mark.asyncio
async def test_reconciliation_ignores_unfinished_failed_and_legacy_status(
    session, admin_user, reporting, tmp_path, monkeypatch, fields
):
    await _enable(session)
    monkeypatch.setattr(settings, "update_status_dir", str(tmp_path))
    _write_status(tmp_path, **fields)

    assert await upgrades.reconcile_from_status(session, now=COMPLETED + timedelta(hours=1)) is None
    assert await _upgrade_reports(session) == []


@pytest.mark.parametrize("content", ["{not json", "[]", '"completed"', "x" * (2 * 1024 * 1024)])
@pytest.mark.asyncio
async def test_reconciliation_survives_a_malformed_status_file(
    session, admin_user, reporting, tmp_path, monkeypatch, content
):
    await _enable(session)
    monkeypatch.setattr(settings, "update_status_dir", str(tmp_path))
    (tmp_path / "current.json").write_text(content)

    assert await upgrades.reconcile_from_status(session, now=COMPLETED) is None


@pytest.mark.asyncio
async def test_reconciliation_does_not_follow_a_symlinked_status(session, admin_user, reporting, tmp_path, monkeypatch):
    await _enable(session)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _write_status(elsewhere)
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    os.symlink(elsewhere / "current.json", status_dir / "current.json")
    monkeypatch.setattr(settings, "update_status_dir", str(status_dir))

    assert await upgrades.reconcile_from_status(session, now=COMPLETED) is None


@pytest.mark.asyncio
async def test_reconciliation_ignores_a_completion_older_than_the_report_expiry(
    session, admin_user, reporting, tmp_path, monkeypatch
):
    await _enable(session)
    monkeypatch.setattr(settings, "update_status_dir", str(tmp_path))
    _write_status(tmp_path)

    assert await upgrades.reconcile_from_status(session, now=COMPLETED + timedelta(days=8)) is None


@pytest.mark.asyncio
async def test_the_cli_reserves_without_network_and_refuses_bad_arguments(session, admin_user, reporting, capsys):
    from app.cli import record_upgrade_metric as cli

    await _enable(session)
    argv = [
        "--operation-id", OP,
        "--from-version", "2026.11.26",
        "--to-version", "2026.12.1",
        "--started-at", STARTED.isoformat(),
        "--completed-at", COMPLETED.isoformat(),
    ]  # fmt: skip

    assert await cli.run(argv, database.async_session_factory, now=COMPLETED) == 0
    assert len(await _upgrade_reports(session)) == 1

    bad = list(argv)
    bad[1] = "SECRET-not-a-uuid"
    assert await cli.run(bad, database.async_session_factory, now=COMPLETED) != 0
    assert "SECRET" not in capsys.readouterr().err


@pytest.mark.asyncio
async def test_an_opted_in_installation_whose_setup_is_unfinished_records_no_upgrade(session, reporting):
    from app.models.organization import Organization

    session.add(Organization(name="Lab", setup_complete=False))
    await session.commit()
    await _enable(session)

    assert await _record(session) == "setup_incomplete"
    assert await _upgrade_reports(session) == []
