"""Settings > System Settings: the installation metrics choice over HTTP (ADR-075).

Only a human admin session with effective ``infrastructure:configure`` reads or changes it. The
mutation accepts the choice, the notice version and the revision, nothing else; the backend owns IDs,
dates, destinations and payloads.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.install_metric_event import InstallMetricEvent
from app.services.install_metrics.state import NOTICE_VERSION

URL = "/api/system-settings"
PUT_URL = "/api/system-settings/install-metrics"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _put(client, token, **body):
    return await client.put(PUT_URL, json=body, headers=_auth(token))


@pytest.mark.asyncio
async def test_an_admin_reads_the_pending_choice_and_the_current_notice(client, admin_token):
    response = await client.get(URL, headers=_auth(admin_token))

    assert response.status_code == 200
    metrics = response.json()["install_metrics"]
    assert metrics["preference"] == "pending"
    assert metrics["prompt_required"] is True
    assert metrics["effective"] is False
    assert metrics["installation_id"] is None
    assert metrics["notice_version"] == NOTICE_VERSION
    assert metrics["revision"] == 0
    # The test settings carry the operator override, as CI does.
    assert metrics["operator_override"] is True


@pytest.mark.asyncio
async def test_sharing_commits_the_choice_and_resolves_the_prompt(client, admin_token):
    response = await _put(client, admin_token, choice="enabled", notice_version=NOTICE_VERSION, revision=0)

    assert response.status_code == 200
    metrics = response.json()["install_metrics"]
    assert metrics["preference"] == "enabled"
    assert metrics["prompt_required"] is False
    assert metrics["installation_id"] is not None
    assert metrics["decided_at"] is not None
    assert metrics["revision"] == 1

    again = (await client.get(URL, headers=_auth(admin_token))).json()["install_metrics"]
    assert again == metrics


@pytest.mark.asyncio
async def test_declining_resolves_the_prompt_without_an_id(client, admin_token):
    response = await _put(client, admin_token, choice="disabled", notice_version=NOTICE_VERSION, revision=0)

    assert response.status_code == 200
    metrics = response.json()["install_metrics"]
    assert metrics["preference"] == "disabled"
    assert metrics["prompt_required"] is False
    assert metrics["installation_id"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"notice_version": NOTICE_VERSION, "revision": 0},
        {"choice": None, "notice_version": NOTICE_VERSION, "revision": 0},
        {"choice": "pending", "notice_version": NOTICE_VERSION, "revision": 0},
        {"choice": "enabled", "revision": 0},
        {"choice": "enabled", "notice_version": NOTICE_VERSION},
        # The backend owns IDs, dates and destinations: a client cannot supply them.
        {"choice": "enabled", "notice_version": NOTICE_VERSION, "revision": 0, "installation_id": "x"},
        {"choice": "enabled", "notice_version": NOTICE_VERSION, "revision": 0, "decided_at": "2020-01-01"},
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_choice_is_refused_and_changes_nothing(client, admin_token, body):
    response = await client.put(PUT_URL, json=body, headers=_auth(admin_token))

    assert response.status_code == 422
    metrics = (await client.get(URL, headers=_auth(admin_token))).json()["install_metrics"]
    assert metrics["preference"] == "pending"
    assert metrics["revision"] == 0


@pytest.mark.asyncio
async def test_an_outdated_notice_version_is_refused(client, admin_token):
    response = await _put(client, admin_token, choice="enabled", notice_version="2000-01-01", revision=0)

    assert response.status_code == 409
    assert response.json()["detail"] == "notice_version_mismatch"


@pytest.mark.asyncio
async def test_a_stale_revision_is_a_conflict_and_keeps_the_first_choice(client, admin_token):
    await _put(client, admin_token, choice="disabled", notice_version=NOTICE_VERSION, revision=0)

    response = await _put(client, admin_token, choice="enabled", notice_version=NOTICE_VERSION, revision=0)

    assert response.status_code == 409
    assert response.json()["detail"] == "revision_conflict"
    metrics = (await client.get(URL, headers=_auth(admin_token))).json()["install_metrics"]
    assert metrics["preference"] == "disabled"


@pytest.mark.asyncio
async def test_viewers_cannot_read_or_change_the_setting(client, viewer_token):
    assert (await client.get(URL, headers=_auth(viewer_token))).status_code == 403
    response = await _put(client, viewer_token, choice="enabled", notice_version=NOTICE_VERSION, revision=0)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_service_account_key_cannot_change_the_setting(client, integration_api_key):
    response = await client.put(
        PUT_URL,
        json={"choice": "enabled", "notice_version": NOTICE_VERSION, "revision": 0},
        headers=integration_api_key["headers"],
    )
    assert response.status_code in (401, 403)
    assert (await client.get(URL, headers=integration_api_key["headers"])).status_code in (401, 403)


@pytest.mark.asyncio
async def test_the_audit_names_the_admin_but_the_reports_never_do(client, admin_token, admin_user, session):
    await _put(client, admin_token, choice="enabled", notice_version=NOTICE_VERSION, revision=0)

    audit = (await session.execute(select(AuditLog).where(AuditLog.entity_type == "installation_metrics"))).scalar_one()
    assert audit.user_id == admin_user.id
    assert audit.details_json == {"choice": "enabled", "notice_version": NOTICE_VERSION}
    reports = (await session.execute(select(InstallMetricEvent.payload))).scalars().all()
    assert all(admin_user.email not in str(p) for p in reports)


@pytest.mark.asyncio
async def test_choosing_never_restricts_the_product(client, admin_token):
    await _put(client, admin_token, choice="disabled", notice_version=NOTICE_VERSION, revision=0)

    assert (await client.get("/api/auth/me", headers=_auth(admin_token))).status_code == 200
    assert (await client.get("/api/experiments", headers=_auth(admin_token))).status_code == 200


@pytest.mark.asyncio
async def test_no_report_exists_before_or_after_declining(client, admin_token, session):
    await _put(client, admin_token, choice="disabled", notice_version=NOTICE_VERSION, revision=0)

    count = (await session.execute(select(func.count()).select_from(InstallMetricEvent))).scalar_one()
    assert count == 0
