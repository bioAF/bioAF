"""Delivery of reserved installation metrics reports (ADR-075).

Every test here sends through an injected ``httpx.MockTransport``; an unmatched or unexpected call
fails the test rather than reaching a network. Consent and the operator override are rechecked before
each send, and a failure never changes anything but the report's own retry state.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, text

import app.database as database
from app.config import settings
from app.models.install_metric_event import InstallMetricEvent
from app.services.install_metrics import sender
from app.services.install_metrics.state import NOTICE_VERSION, InstallMetricsService

NOW = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)
TOKEN = "phc_testTokenForInstallMetrics0000000000000"
HOST = "https://ingest.test.example"


class Capture:
    """A PostHog stand-in: records each request and answers with the next scripted outcome."""

    def __init__(self, *outcomes):
        self.requests: list[httpx.Request] = []
        self.outcomes = list(outcomes)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, json={"status": "Ok"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def metrics_on(monkeypatch):
    """This test's settings: override off, a test destination. Nothing else changes."""
    monkeypatch.setattr(settings, "install_metrics_disabled", False)
    monkeypatch.setattr(settings, "posthog_project_token", TOKEN)
    monkeypatch.setattr(settings, "posthog_host", HOST)


async def _opt_in(session, now=NOW):
    state = await InstallMetricsService.read(session)
    state = await InstallMetricsService.choose(
        session, choice="enabled", notice_version=NOTICE_VERSION, revision=state.revision, now=now
    )
    await session.commit()
    return state


async def _rows(session):
    session.expire_all()
    return (await session.execute(select(InstallMetricEvent).order_by(InstallMetricEvent.id))).scalars().all()


async def _send(capture, now=NOW):
    return await sender.send_pending(database.async_session_factory, now=now, transport=capture.transport)


@pytest.mark.asyncio
async def test_an_opted_in_baseline_is_sent_once_with_the_token_attached_only_at_dispatch(
    session, admin_user, metrics_on
):
    state = await _opt_in(session)
    capture = Capture(200)

    await _send(capture)
    await _send(capture)

    assert len(capture.requests) == 1
    request = capture.requests[0]
    assert str(request.url) == f"{HOST}/i/v0/e/"
    body = capture.bodies()[0]
    assert body["api_key"] == TOKEN
    assert body["event"] == "bioaf_installation_observed"
    assert body["distinct_id"] == state.installation_id
    assert body["timestamp"] == "2026-11-20T00:00:00Z"
    (row,) = await _rows(session)
    assert row.status == "sent"
    assert row.payload is None
    assert "api_key" not in json.dumps(row.payload)


@pytest.mark.asyncio
async def test_a_failed_send_retries_the_identical_event_with_capped_backoff(session, admin_user, metrics_on):
    await _opt_in(session)
    capture = Capture(httpx.ConnectTimeout("t"), 503, 200)

    await _send(capture)
    (row,) = await _rows(session)
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.last_error == "timeout"
    first_retry = row.next_attempt_at
    assert first_retry > NOW

    await _send(capture, now=NOW + timedelta(seconds=1))  # not due yet
    assert len(capture.requests) == 1

    await _send(capture, now=first_retry)
    (row,) = await _rows(session)
    assert row.last_error == "http_503"
    await _send(capture, now=row.next_attempt_at)

    bodies = capture.bodies()
    assert len(bodies) == 3
    assert bodies[0] == bodies[1] == bodies[2]
    (row,) = await _rows(session)
    assert row.status == "sent"


def test_backoff_doubles_and_is_capped_at_a_day():
    delays = [sender.backoff(n) for n in range(1, 20)]

    assert delays[0] < delays[1] < delays[2]
    assert max(delays) == timedelta(hours=24)


@pytest.mark.asyncio
async def test_at_most_five_reports_are_sent_per_pass(session, admin_user, metrics_on):
    state = await _opt_in(session)
    from app.services.install_metrics import outbox

    for i in range(7):
        await outbox.reserve(
            session,
            source_key=outbox.upgrade_source(state.installation_id, f"00000000-0000-4000-8000-00000000000{i}"),
            name="bioaf_upgrade_completed",
            installation_id=state.installation_id,
            moment=NOW,
            bioaf_version="2026.11.27",
            from_version="2026.11.26",
        )
    await session.commit()
    capture = Capture()

    await _send(capture)

    assert len(capture.requests) == 5


@pytest.mark.asyncio
async def test_a_report_older_than_seven_days_expires_unsent(session, admin_user, metrics_on):
    await _opt_in(session)
    capture = Capture()

    await _send(capture, now=NOW + timedelta(days=7, seconds=1))

    assert capture.requests == []
    (row,) = await _rows(session)
    assert row.status == "expired"
    assert row.payload is None


@pytest.mark.asyncio
async def test_withdrawal_before_dispatch_cancels_and_sends_nothing(session, admin_user, metrics_on):
    state = await _opt_in(session)
    await InstallMetricsService.choose(
        session, choice="disabled", notice_version=NOTICE_VERSION, revision=state.revision, now=NOW
    )
    await session.commit()
    capture = Capture()

    await _send(capture)

    assert capture.requests == []
    (row,) = await _rows(session)
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_re_enabling_never_replays_a_cancelled_baseline(session, admin_user, metrics_on):
    state = await _opt_in(session)
    state = await InstallMetricsService.choose(
        session, choice="disabled", notice_version=NOTICE_VERSION, revision=state.revision, now=NOW
    )
    await session.commit()
    again = await InstallMetricsService.choose(
        session, choice="enabled", notice_version=NOTICE_VERSION, revision=state.revision, now=NOW
    )
    await session.commit()
    capture = Capture()

    await _send(capture)

    assert capture.requests == []
    assert again.installation_id == state.installation_id
    assert [r.status for r in await _rows(session)] == ["cancelled"]


@pytest.mark.asyncio
async def test_the_operator_override_cancels_pending_reports_and_lifting_it_backfills_nothing(
    session, admin_user, metrics_on, monkeypatch
):
    await _opt_in(session)
    monkeypatch.setattr(settings, "install_metrics_disabled", True)
    capture = Capture()

    await _send(capture)
    monkeypatch.setattr(settings, "install_metrics_disabled", False)
    await _send(capture, now=NOW + timedelta(hours=1))

    assert capture.requests == []
    assert [r.status for r in await _rows(session)] == ["cancelled"]
    state = await InstallMetricsService.read(session)
    assert state.enabled_since == NOW + timedelta(hours=1)


@pytest.mark.asyncio
async def test_an_override_during_setup_leaves_a_current_observation_for_later(session, metrics_on, monkeypatch):
    """Setup completed with consent but under the override: lifting it reports a current observation."""
    from app.models.organization import Organization

    session.add(Organization(name="Lab", setup_complete=False))
    await session.commit()
    monkeypatch.setattr(settings, "install_metrics_disabled", True)
    await _opt_in(session)
    await InstallMetricsService.setup_completed(session, now=NOW)
    await session.commit()
    assert await _rows(session) == []

    monkeypatch.setattr(settings, "install_metrics_disabled", False)
    capture = Capture()
    later = NOW + timedelta(days=3)
    await _send(capture, now=later)

    (body,) = capture.bodies()
    assert body["event"] == "bioaf_installation_observed"
    assert body["timestamp"] == "2026-11-23T00:00:00Z"


@pytest.mark.asyncio
async def test_pending_or_declined_installations_make_no_transport_calls(session, admin_user, metrics_on):
    capture = Capture()
    await _send(capture)
    await InstallMetricsService.choose(session, choice="disabled", notice_version=NOTICE_VERSION, revision=0)
    await session.commit()
    await _send(capture)

    assert capture.requests == []
    assert await _rows(session) == []
    assert (await InstallMetricsService.read(session)).installation_id is None


@pytest.mark.asyncio
async def test_without_a_valid_destination_reports_wait_and_nothing_is_sent(
    session, admin_user, metrics_on, monkeypatch
):
    await _opt_in(session)
    monkeypatch.setattr(settings, "posthog_host", "")  # a partial override
    capture = Capture()

    await _send(capture)

    assert capture.requests == []
    (row,) = await _rows(session)
    assert row.status == "pending"


@pytest.mark.asyncio
async def test_a_stored_report_outside_the_allowlist_is_never_sent(session, admin_user, metrics_on, caplog):
    await _opt_in(session)
    await session.execute(
        text(
            "UPDATE install_metric_events SET payload = jsonb_set(payload, '{properties,email}', '\"admin@test.com\"')"
        )
    )
    await session.commit()
    capture = Capture()

    await _send(capture)

    assert capture.requests == []
    (row,) = await _rows(session)
    assert row.status == "cancelled"
    assert row.last_error == "payload_rejected"
    assert "admin@test.com" not in caplog.text


@pytest.mark.asyncio
async def test_diagnostics_never_include_the_token_payload_or_exception_text(session, admin_user, metrics_on, caplog):
    state = await _opt_in(session)
    capture = Capture(httpx.ConnectError(f"cannot reach {HOST} with {TOKEN}"), 500)

    await _send(capture)
    (row,) = await _rows(session)
    await _send(capture, now=row.next_attempt_at)

    for secret in (TOKEN, state.installation_id, "cannot reach", HOST):
        assert secret not in caplog.text
    (row,) = await _rows(session)
    assert row.last_error == "http_500"


@pytest.mark.asyncio
async def test_private_values_in_the_installation_never_reach_the_outgoing_bytes(session, admin_user, metrics_on):
    """Seed identities, science and custom builds everywhere they could leak from; inspect every byte."""
    from app.models.experiment import Experiment
    from app.models.project import Project

    project = Project(organization_id=admin_user.organization_id, name="SECRET-PROJECT-ALPHA", status="active")
    session.add(project)
    await session.flush()
    session.add(
        Experiment(
            organization_id=admin_user.organization_id,
            project_id=project.id,
            name="SECRET-EXPERIMENT-CRISPR-KO",
            status="registered",
        )
    )
    admin_user.name = "Dr. Secret Person"
    await session.commit()
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('gcp_project_id', 'secret-gcp-project-123') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()
    original = settings.app_version
    settings.app_version = "2026.11.27-secret-branch"
    try:
        await _opt_in(session)
    finally:
        settings.app_version = original
    capture = Capture()

    await _send(capture)

    sent = b"".join(r.content + str(r.headers).encode() + str(r.url).encode() for r in capture.requests)
    for private in (
        "SECRET-PROJECT-ALPHA",
        "SECRET-EXPERIMENT-CRISPR-KO",
        "Dr. Secret Person",
        "admin@test.com",
        "Test Org",
        "secret-gcp-project-123",
        "secret-branch",
    ):
        assert private.encode() not in sent
    assert capture.bodies()[0]["properties"]["bioaf_version"] == "unknown"


@pytest.mark.asyncio
async def test_no_database_transaction_is_open_during_the_network_call(session, admin_user, metrics_on):
    await _opt_in(session)
    opened: list = []
    seen: list[bool] = []

    def tracking_factory():
        own = database.async_session_factory()
        opened.append(own)
        return own

    class Probe(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(any(s.in_transaction() for s in opened))
            return httpx.Response(200)

    await sender.send_pending(tracking_factory, now=NOW, transport=Probe())

    assert seen == [False]
