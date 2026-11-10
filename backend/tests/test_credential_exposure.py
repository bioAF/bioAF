"""Credentials never reach a connection-test response or a delivery log.

Two reproduced exposure paths: a failed PubMed connection test echoed the
request URL (and its api_key) through the raw exception text, and a failed
Slack incoming-webhook delivery logged the raw exception, which carries the
credential-bearing webhook URL. Both are exercised through the real clients
with synthetic secrets and controlled HTTP; the log assertions use a capture
handler without redaction as well as the production RedactSecretsFilter, so
neither result depends on the filter knowing the secret.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from app.logging_config import RedactSecretsFilter
from app.services.literature.sources import pubmed
from app.services.notification_channels import slack_adapter
from app.services.notification_channels.slack_adapter import SlackChannel

SYNTHETIC_PUBMED_KEY = "pmkey-SYNTHETIC-0123456789abcdef"
WEBHOOK_SECRET_PATH = "T0SYNTH/B0SYNTH/XsyntheticWebhookSecret42"
WEBHOOK_URL = f"https://hooks.slack.com/services/{WEBHOOK_SECRET_PATH}"


class _Capture(logging.Handler):
    def __init__(self, *filters: logging.Filter) -> None:
        super().__init__(level=logging.DEBUG)
        for f in filters:
            self.addFilter(f)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def captured_logs():
    """Two handlers on the ``bioaf`` logger: one raw, one with the production
    redaction filter. Yields the combined emitted text."""
    raw = _Capture()
    redacted = _Capture(RedactSecretsFilter())
    raw.setFormatter(logging.Formatter("%(message)s"))
    redacted.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("bioaf")
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(raw)
    root.addHandler(redacted)
    try:
        yield lambda: "\n".join(raw.lines + redacted.lines)
    finally:
        root.removeHandler(raw)
        root.removeHandler(redacted)
        root.setLevel(previous_level)


def _fragments(secret: str, size: int = 8) -> list[str]:
    return [secret[i : i + size] for i in range(0, len(secret) - size + 1, 4)]


async def _seed_pubmed_key(session, org_id: int, user_id: int) -> None:
    from app.services.bootstrap_literature import seed_literature_sources
    from app.services.literature import sources_config_service

    await seed_literature_sources(session, org_id)
    await sources_config_service.update(
        session, org_id=org_id, source="pubmed", user_id=user_id, api_key=SYNTHETIC_PUBMED_KEY
    )
    await session.commit()


@pytest.mark.parametrize("failure", ["status_503", "status_429", "connect_error", "read_timeout"])
@pytest.mark.asyncio
async def test_a_failed_pubmed_connection_test_never_returns_the_api_key(
    failure, client, comp_bio_token, comp_bio_user, session, captured_logs
):
    """comp_bio holds literature:configure_sources, so this is not an
    admin-only path."""
    await _seed_pubmed_key(session, comp_bio_user.organization_id, comp_bio_user.id)

    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{pubmed._BASE}/esearch.fcgi")
        if failure == "status_503":
            route.respond(status_code=503, text=f"upstream error for api_key={SYNTHETIC_PUBMED_KEY}")
        elif failure == "status_429":
            route.respond(status_code=429)
        elif failure == "connect_error":
            route.mock(side_effect=httpx.ConnectError(f"cannot reach {pubmed._BASE}?api_key={SYNTHETIC_PUBMED_KEY}"))
        else:
            route.mock(side_effect=httpx.ReadTimeout(f"timed out api_key={SYNTHETIC_PUBMED_KEY}"))
        resp = await client.post(
            "/api/literature/sources/pubmed/test", headers={"Authorization": f"Bearer {comp_bio_token}"}
        )
        assert route.called, "the real PubMed client must have dispatched the request"

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert isinstance(body["latency_ms"], int)
    message = body["message"]
    assert message, "the failure must still carry a usable diagnostic"
    exposed = resp.text + captured_logs()
    assert SYNTHETIC_PUBMED_KEY not in exposed
    for fragment in _fragments(SYNTHETIC_PUBMED_KEY):
        assert fragment not in exposed
    assert "api_key" not in exposed
    assert "eutils.ncbi.nlm.nih.gov" not in exposed


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("status_503", "503"), ("status_429", "429"), ("connect_error", "reach"), ("read_timeout", "timed out")],
)
@pytest.mark.asyncio
async def test_a_failed_connection_test_keeps_a_useful_safe_classification(
    failure, expected, client, admin_token, admin_user, session
):
    await _seed_pubmed_key(session, admin_user.organization_id, admin_user.id)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{pubmed._BASE}/esearch.fcgi")
        if failure == "status_503":
            route.respond(status_code=503)
        elif failure == "status_429":
            route.respond(status_code=429)
        elif failure == "connect_error":
            route.mock(side_effect=httpx.ConnectError("boom"))
        else:
            route.mock(side_effect=httpx.ReadTimeout("slow"))
        resp = await client.post(
            "/api/literature/sources/pubmed/test", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert expected in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_a_reachable_source_still_reports_success(client, admin_token, admin_user, session):
    await _seed_pubmed_key(session, admin_user.organization_id, admin_user.id)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{pubmed._BASE}/esearch.fcgi").respond(json={"esearchresult": {"idlist": []}})
        resp = await client.post(
            "/api/literature/sources/pubmed/test", headers={"Authorization": f"Bearer {admin_token}"}
        )
    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "Source reachable; returned 0 result(s)"


@pytest.mark.parametrize("failure", ["status_410", "status_500", "connect_error"])
@pytest.mark.asyncio
async def test_a_failed_slack_webhook_delivery_never_logs_the_webhook_url(failure, monkeypatch, captured_logs):
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(slack_adapter.asyncio, "sleep", fake_sleep)

    with respx.mock(assert_all_called=False) as router:
        route = router.post(WEBHOOK_URL)
        if failure == "status_410":
            route.respond(status_code=410, text="no_service")
        elif failure == "status_500":
            route.respond(status_code=500)
        else:
            route.mock(side_effect=httpx.ConnectError(f"cannot connect to {WEBHOOK_URL}"))
        delivered = await SlackChannel.deliver_webhook(
            webhook_url=WEBHOOK_URL, title="Run failed", message="details", severity="warning"
        )
        attempts = route.call_count

    # Delivery behavior is unchanged by this correction.
    assert delivered is False
    assert attempts == slack_adapter.MAX_RETRIES
    assert delays == [2, 8]

    logs = captured_logs()
    assert logs, "failures must still be logged"
    assert WEBHOOK_SECRET_PATH not in logs
    assert "hooks.slack.com/services" not in logs
    for fragment in _fragments(WEBHOOK_SECRET_PATH):
        assert fragment not in logs
    # The safe diagnostic names the failure class.
    if failure == "connect_error":
        assert "ConnectError" in logs
    else:
        assert failure.split("_")[1] in logs


@pytest.mark.asyncio
async def test_a_successful_slack_webhook_delivery_is_unchanged(monkeypatch, captured_logs):
    with respx.mock(assert_all_called=False) as router:
        route = router.post(WEBHOOK_URL).respond(status_code=200, text="ok")
        delivered = await SlackChannel.deliver_webhook(
            webhook_url=WEBHOOK_URL, title="Run done", message="ok", severity="info"
        )
    assert delivered is True
    assert route.call_count == 1
    assert WEBHOOK_SECRET_PATH not in captured_logs()
