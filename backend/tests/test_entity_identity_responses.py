"""Entity responses carry dual identifiers and explicit parent references (plan: entity identity).

Experiment responses add ``project_external_id`` beside the project ID; sample responses add
``experiment_id`` and ``experiment_external_id``. Each external ID belongs to the ID beside it, and an
internally created record without one says so with null. Existing fields are unchanged.
"""

from __future__ import annotations

import pytest

from app.models.experiment import Experiment
from app.models.project import Project
from app.models.sample import Sample

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _tree(session, org_id, *, project_ext="PRJ-1", exp_ext="007"):
    project = Project(organization_id=org_id, name="P", external_id=project_ext)
    session.add(project)
    await session.flush()
    exp = Experiment(organization_id=org_id, project_id=project.id, name="E", external_id=exp_ext, status="registered")
    loose = Experiment(organization_id=org_id, name="Loose", status="registered")
    session.add_all([exp, loose])
    await session.flush()
    sample = Sample(experiment_id=exp.id, external_id="S-1", status="registered")
    internal = Sample(experiment_id=loose.id, external_id=None, status="registered")
    session.add_all([sample, internal])
    await session.commit()
    return project, exp, loose, sample, internal


async def test_regular_experiment_responses(client, admin_token, session, admin_user):
    project, exp, loose, *_ = await _tree(session, admin_user.organization_id)
    body = (await client.get(f"/api/experiments/{exp.id}", headers=_auth(admin_token))).json()
    assert body["external_id"] == "007"
    assert body["project_id"] == project.id and body["project_external_id"] == "PRJ-1"
    assert body["project"]["id"] == project.id  # the existing shape is kept
    loose_body = (await client.get(f"/api/experiments/{loose.id}", headers=_auth(admin_token))).json()
    assert loose_body["project_id"] is None and loose_body["project_external_id"] is None
    listing = (await client.get("/api/experiments", headers=_auth(admin_token))).json()
    assert {e["id"]: e["project_external_id"] for e in listing["experiments"]}[exp.id] == "PRJ-1"


async def test_regular_sample_responses(client, admin_token, session, admin_user):
    _, exp, loose, sample, internal = await _tree(session, admin_user.organization_id)
    body = (await client.get(f"/api/samples/{sample.id}", headers=_auth(admin_token))).json()
    assert body["experiment_id"] == exp.id and body["experiment_external_id"] == "007"
    rows = (await client.get(f"/api/experiments/{loose.id}/samples", headers=_auth(admin_token))).json()
    assert rows[0]["id"] == internal.id
    assert rows[0]["external_id"] is None and rows[0]["experiment_external_id"] is None


async def test_integration_experiment_responses(client, integration_api_key, session, admin_user):
    headers = integration_api_key["headers"]
    project, exp, *_ = await _tree(session, admin_user.organization_id)
    got = (await client.get(f"/api/v1/integrations/experiments/{exp.id}", headers=headers)).json()
    assert got["project_id"] == project.id and got["project_external_id"] == "PRJ-1"
    created = (
        await client.post(
            "/api/v1/integrations/experiments",
            headers=headers,
            json={"name": "N", "external_id": "0042", "project_external_id": "PRJ-1"},
        )
    ).json()
    assert created["external_id"] == "0042" and created["project_external_id"] == "PRJ-1"
    listing = (await client.get("/api/v1/integrations/experiments", headers=headers)).json()
    assert all("project_external_id" in item for item in listing["items"])


async def test_integration_sample_responses(client, integration_api_key, session, admin_user):
    headers = integration_api_key["headers"]
    _, exp, _, sample, _ = await _tree(session, admin_user.organization_id)
    got = (await client.get(f"/api/v1/integrations/samples/{sample.id}", headers=headers)).json()
    assert got["experiment_id"] == exp.id and got["experiment_external_id"] == "007"
    created = (
        await client.post(
            "/api/v1/integrations/samples", headers=headers, json={"experiment_id": exp.id, "external_id": "S-2"}
        )
    ).json()
    assert created["experiment_external_id"] == "007"
    listing = (await client.get("/api/v1/integrations/samples", headers=headers)).json()
    assert all(item["experiment_external_id"] == "007" for item in listing["items"] if item["experiment_id"] == exp.id)
