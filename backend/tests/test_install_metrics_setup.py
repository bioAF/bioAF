"""The installation baseline at setup completion (ADR-075).

Both wizard completion paths (infrastructure deferred, or components selected) end at
``POST /api/bootstrap/complete``. Only that completion, with consent given and reporting effective,
reserves the one ``bioaf_installation_completed`` report. A failure in metrics never changes the
setup outcome.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.models.install_metric_event import InstallMetricEvent
from app.models.organization import Organization
from app.services.install_metrics import state as metrics_state
from app.services.install_metrics.state import NOTICE_VERSION, InstallMetricsService


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def mid_setup(session, admin_user, monkeypatch):
    """An installation inside the setup wizard, reporting not overridden."""
    monkeypatch.setattr(settings, "install_metrics_disabled", False)
    org = await session.get(Organization, admin_user.organization_id)
    org.setup_complete = False
    await session.commit()
    return org


async def _choose(client, token, choice):
    current = (await client.get("/api/system-settings", headers=_auth(token))).json()["install_metrics"]
    response = await client.put(
        "/api/system-settings/install-metrics",
        json={"choice": choice, "notice_version": NOTICE_VERSION, "revision": current["revision"]},
        headers=_auth(token),
    )
    assert response.status_code == 200
    return response.json()["install_metrics"]


async def _reports(session):
    session.expire_all()
    return (await session.execute(select(InstallMetricEvent))).scalars().all()


@pytest.mark.asyncio
async def test_sharing_in_the_wizard_reserves_one_completed_baseline_at_completion(
    client, admin_token, session, mid_setup
):
    chosen = await _choose(client, admin_token, "enabled")
    assert chosen["setup_completed"] is False
    assert await _reports(session) == [], "choosing during the wizard is not completion evidence"

    for _ in range(3):  # repeated submissions
        assert (await client.post("/api/bootstrap/complete", headers=_auth(admin_token))).status_code == 200

    (report,) = await _reports(session)
    assert report.event_name == "bioaf_installation_completed"
    assert report.payload["distinct_id"] == chosen["installation_id"]
    assert report.payload["properties"]["$set_once"]["registration_kind"] == "new"


@pytest.mark.asyncio
async def test_declining_in_the_wizard_completes_setup_with_nothing_reserved(client, admin_token, session, mid_setup):
    await _choose(client, admin_token, "disabled")

    assert (await client.post("/api/bootstrap/complete", headers=_auth(admin_token))).status_code == 200

    assert await _reports(session) == []
    state = await InstallMetricsService.read(session)
    assert state.installation_id is None
    assert state.setup_completed is True


@pytest.mark.asyncio
async def test_a_headless_setup_without_a_choice_reports_nothing_and_stays_pending(
    client, admin_token, session, mid_setup
):
    assert (await client.post("/api/bootstrap/complete", headers=_auth(admin_token))).status_code == 200

    assert await _reports(session) == []
    state = await InstallMetricsService.read(session)
    assert state.preference == "pending"
    assert state.installation_id is None


@pytest.mark.asyncio
async def test_sharing_after_setup_completed_reports_a_current_observation_not_an_installation(
    client, admin_token, session, mid_setup
):
    await client.post("/api/bootstrap/complete", headers=_auth(admin_token))

    chosen = await _choose(client, admin_token, "enabled")

    assert chosen["setup_completed"] is True
    (report,) = await _reports(session)
    assert report.event_name == "bioaf_installation_observed"
    assert report.payload["properties"]["$set_once"]["registration_kind"] == "existing"


@pytest.mark.asyncio
async def test_an_abandoned_setup_reports_nothing(client, admin_token, session, mid_setup):
    await _choose(client, admin_token, "enabled")

    # The admin never reaches completion.
    assert await _reports(session) == []


@pytest.mark.asyncio
async def test_a_metrics_failure_never_fails_setup_completion(client, admin_token, session, mid_setup, monkeypatch):
    await _choose(client, admin_token, "enabled")
    org_id = mid_setup.id

    async def broken(*args, **kwargs):
        raise RuntimeError("metrics store unavailable")

    monkeypatch.setattr(metrics_state.InstallMetricsService, "setup_completed", staticmethod(broken))

    response = await client.post("/api/bootstrap/complete", headers=_auth(admin_token))

    assert response.status_code == 200
    session.expire_all()
    org = await session.get(Organization, org_id)
    assert org.setup_complete is True
    assert await _reports(session) == []


@pytest.mark.asyncio
async def test_an_existing_installation_completing_setup_again_is_an_observation(
    client, admin_token, session, admin_user, monkeypatch
):
    """Setup already complete before the metrics release: no installation completion is claimed."""
    monkeypatch.setattr(settings, "install_metrics_disabled", False)

    chosen = await _choose(client, admin_token, "enabled")
    await client.post("/api/bootstrap/complete", headers=_auth(admin_token))

    (report,) = await _reports(session)
    assert report.event_name == "bioaf_installation_observed"
    assert report.payload["distinct_id"] == chosen["installation_id"]
