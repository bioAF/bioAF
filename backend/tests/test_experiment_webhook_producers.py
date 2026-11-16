"""01C.1: experiment.created / experiment.updated / experiment.status_changed from every reachable owner.

Each test performs a real mutation (UI API, Integration API, or the shared service), commits, and
reads the persisted webhook deliveries back. Payloads must carry exactly the public fields, with the
subject's own external ID and its project's pair, and only actual changes produce events.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.schemas.experiment import ExperimentCreate
from app.services.event_types import EXPERIMENT_STATUS_CHANGED
from app.services.experiment_service import ExperimentService
from tests.support.event_capture import EventCapture
from tests.support.webhook_capture import WebhookCapture

pytestmark = pytest.mark.asyncio

CREATED, UPDATED, STATUS = "experiment.created", "experiment.updated", "experiment.status_changed"


@pytest_asyncio.fixture
async def capture(session, admin_user):
    c = await WebhookCapture.start(session, admin_user)
    yield c
    c.close()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _project(session, org_id, external_id="PRJ-1"):
    p = Project(organization_id=org_id, name="P", external_id=external_id)
    session.add(p)
    await session.commit()
    return p


# --- UI / service -----------------------------------------------------------------------------


async def test_ui_create_announces_the_experiment_with_its_project(client, admin_token, session, admin_user, capture):
    project = await _project(session, admin_user.organization_id)
    r = await client.post(
        "/api/experiments",
        headers=_auth(admin_token),
        json={"name": "E1", "project_id": project.id, "custom_fields": [{"field_name": "k", "field_value": "v"}]},
    )
    assert r.status_code == 200, r.text
    exp_id = r.json()["id"]

    assert await capture.deliveries() == [
        (
            CREATED,
            {"experiment_id": exp_id, "external_id": None, "project_id": project.id, "project_external_id": "PRJ-1"},
        )
    ]


async def test_a_rolled_back_create_announces_nothing(session, admin_user, capture):
    await ExperimentService.create_experiment(
        session, admin_user.organization_id, admin_user.id, ExperimentCreate(name="X")
    )
    await session.rollback()
    assert await capture.deliveries() == []


async def test_updates_announce_only_actual_changes(client, admin_token, session, admin_user, capture):
    r = await client.post("/api/experiments", headers=_auth(admin_token), json={"name": "E1", "hypothesis": "h"})
    exp_id = r.json()["id"]
    base = len(await capture.deliveries())

    # Assigning the same values changes nothing.
    await client.patch(f"/api/experiments/{exp_id}", headers=_auth(admin_token), json={"name": "E1", "hypothesis": "h"})
    assert len(await capture.deliveries()) == base

    await client.patch(
        f"/api/experiments/{exp_id}",
        headers=_auth(admin_token),
        json={"name": "E2", "hypothesis": "h", "custom_fields": [{"field_name": "k", "field_value": "v"}]},
    )
    updates = await capture.data(UPDATED)
    assert updates == [
        {
            "experiment_id": exp_id,
            "external_id": None,
            "project_id": None,
            "project_external_id": None,
            "changed_fields": ["custom_fields", "name"],
        }
    ]

    # Equivalent custom fields again: no event.
    await client.patch(
        f"/api/experiments/{exp_id}",
        headers=_auth(admin_token),
        json={"custom_fields": [{"field_name": "k", "field_value": "v"}]},
    )
    assert len(await capture.data(UPDATED)) == 1


async def test_status_changes_carry_identity_and_keep_notification_fields(
    client, admin_token, session, admin_user, capture
):
    project = await _project(session, admin_user.organization_id)
    exp = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project.id,
        name="E",
        external_id="LIMS-EXP-42",
        status="registered",
    )
    session.add(exp)
    await session.commit()
    notices = EventCapture([EXPERIMENT_STATUS_CHANGED])
    try:
        r = await client.patch(
            f"/api/experiments/{exp.id}/status", headers=_auth(admin_token), json={"status": "library_prep"}
        )
        assert r.status_code == 200, r.text
        await notices.settle()
    finally:
        notices.close()

    assert await capture.data(STATUS) == [
        {
            "experiment_id": exp.id,
            "external_id": "LIMS-EXP-42",
            "project_id": project.id,
            "project_external_id": "PRJ-1",
            "old_status": "registered",
            "new_status": "library_prep",
        }
    ]
    [notice] = notices.of(EXPERIMENT_STATUS_CHANGED)
    for key in ("org_id", "user_id", "entity_type", "entity_id", "title", "message", "summary"):
        assert key in notice
    assert notice["metadata"] == {"old_status": "registered", "new_status": "library_prep"}


async def test_an_invalid_transition_announces_nothing(client, admin_token, session, admin_user, capture):
    exp = Experiment(organization_id=admin_user.organization_id, name="E", status="registered")
    session.add(exp)
    await session.commit()
    r = await client.patch(f"/api/experiments/{exp.id}/status", headers=_auth(admin_token), json={"status": "complete"})
    assert r.status_code == 400
    assert await capture.deliveries() == []


async def test_distinct_transitions_in_one_transaction_each_publish(session, admin_user, capture):
    exp = Experiment(organization_id=admin_user.organization_id, name="E", status="registered")
    session.add(exp)
    await session.commit()
    for status in ("library_prep", "sequencing"):
        await ExperimentService.update_status(session, exp.id, admin_user.organization_id, admin_user.id, status)
    await session.commit()
    assert [(d["old_status"], d["new_status"]) for d in await capture.data(STATUS)] == [
        ("registered", "library_prep"),
        ("library_prep", "sequencing"),
    ]


async def test_soft_delete_is_a_status_change(session, admin_user, capture):
    exp = Experiment(organization_id=admin_user.organization_id, name="E", status="analysis")
    session.add(exp)
    await session.commit()
    await ExperimentService.delete_experiment(session, exp.id, admin_user.organization_id, admin_user.id)
    await session.commit()
    [data] = await capture.data(STATUS)
    assert (data["old_status"], data["new_status"]) == ("analysis", "deleted")


async def test_an_approving_review_announces_reviewed(session, admin_user, capture):
    from app.services.pipeline_review_service import PipelineReviewService

    exp = Experiment(organization_id=admin_user.organization_id, name="E", status="pipeline_complete")
    session.add(exp)
    await session.flush()
    run = PipelineRun(
        organization_id=admin_user.organization_id, experiment_id=exp.id, pipeline_name="rnaseq", status="completed"
    )
    session.add(run)
    await session.commit()
    await PipelineReviewService.create_review(
        session, run.id, admin_user.organization_id, admin_user.id, "approved", None, None, None
    )
    await session.commit()
    [data] = await capture.data(STATUS)
    assert (data["old_status"], data["new_status"]) == ("pipeline_complete", "reviewed")


# --- Integration API --------------------------------------------------------------------------


async def test_integration_create_announces_once_and_replays_announce_nothing(
    client, integration_api_key, session, admin_user, capture
):
    headers = {**integration_api_key["headers"], "Idempotency-Key": "k-1"}
    project = await _project(session, admin_user.organization_id)
    body = {"name": "E", "external_id": "007", "project_id": project.id}
    first = await client.post("/api/v1/integrations/experiments", headers=headers, json=body)
    assert first.status_code == 201, first.text
    replay = await client.post("/api/v1/integrations/experiments", headers=headers, json=body)
    assert replay.headers.get("Idempotency-Replayed") == "true"

    assert await capture.deliveries() == [
        (
            CREATED,
            {
                "experiment_id": first.json()["id"],
                "external_id": "007",
                "project_id": project.id,
                "project_external_id": "PRJ-1",
            },
        )
    ]


async def test_integration_patch_announces_actual_changes(client, integration_api_key, session, admin_user, capture):
    headers = integration_api_key["headers"]
    r = await client.post("/api/v1/integrations/experiments", headers=headers, json={"name": "E", "external_id": "X-1"})
    exp_id = r.json()["id"]

    await client.patch(f"/api/v1/integrations/experiments/{exp_id}", headers=headers, json={"name": "E"})
    await client.patch(
        f"/api/v1/integrations/experiments/{exp_id}",
        headers=headers,
        json={"description": "d", "custom_fields": [{"field_name": "a", "field_value": "1"}]},
    )
    rejected = await client.patch(
        f"/api/v1/integrations/experiments/{exp_id}", headers=headers, json={"status": "complete"}
    )
    assert rejected.status_code == 400

    assert [d["changed_fields"] for d in await capture.data(UPDATED)] == [["custom_fields", "description"]]


# --- claims -----------------------------------------------------------------------------------


async def test_claiming_an_existing_experiment_announces_its_changes(client, admin_token, session, admin_user, capture):
    exp = Experiment(organization_id=admin_user.organization_id, name="auto", is_unclaimed=True, status="registered")
    session.add(exp)
    await session.commit()
    r = await client.post(f"/api/experiments/{exp.id}/claim", headers=_auth(admin_token), json={"name": "Mine"})
    assert r.status_code == 200, r.text
    [data] = await capture.data(UPDATED)
    assert data["changed_fields"] == ["is_unclaimed", "name", "owner_user_id"]


async def test_another_organizations_experiment_cannot_be_claimed(client, admin_token, session, capture):
    other = Organization(name="Other")
    session.add(other)
    await session.flush()
    exp = Experiment(organization_id=other.id, name="theirs", is_unclaimed=True, status="registered")
    session.add(exp)
    await session.commit()
    r = await client.post(f"/api/experiments/{exp.id}/claim", headers=_auth(admin_token), json={"name": "Mine"})
    assert r.status_code == 404
    await session.refresh(exp)
    assert exp.is_unclaimed is True


async def test_events_stay_in_their_organization(session, admin_user, capture):
    other = Organization(name="Other")
    session.add(other)
    await session.commit()
    await ExperimentService.create_experiment(session, other.id, admin_user.id, ExperimentCreate(name="Theirs"))
    await session.commit()
    assert await capture.deliveries() == []
