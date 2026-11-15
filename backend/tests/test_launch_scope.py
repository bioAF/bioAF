# ruff: noqa: F811 -- pytest fixtures imported from the notebook and work-node suites
"""Launch scope for notebooks and work nodes (issue #103; app/services/launch_scope.py).

- A notebook launch records the project it was launched in, directly or through its experiment's own project;
  a standalone experiment (no project) stays standalone.
- Before any session row or provider call, both launches refuse a project, experiment or environment version
  outside the caller's organization exactly as they refuse a missing one, and refuse an experiment whose project
  is not the one given.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.notebook_session import ComputeSession
from app.models.organization import Organization
from app.models.project import Project
from tests.test_notebook_sessions_v1 import seed_platform_config  # noqa: F401 -- fixture
from tests.test_work_nodes import seed_environment, seed_session_credentials  # noqa: F401 -- fixtures

pytestmark = pytest.mark.asyncio

NOTEBOOKS = "/api/v1/notebooks/sessions"
WORK_NODES = "/api/v1/work-nodes/sessions"


async def _project(session, org_id: int, name: str) -> Project:
    project = Project(name=name, organization_id=org_id)
    session.add(project)
    await session.flush()
    return project


async def _experiment(session, org_id: int, name: str, project_id: int | None = None) -> Experiment:
    exp = Experiment(name=name, organization_id=org_id, project_id=project_id)
    session.add(exp)
    await session.flush()
    return exp


async def _other_org(session) -> int:
    org = Organization(name="Elsewhere", setup_complete=True)
    session.add(org)
    await session.flush()
    return org.id


async def _sessions(session) -> int:
    return (await session.execute(select(func.count()).select_from(ComputeSession))).scalar_one()


@pytest.fixture
def provider_calls(monkeypatch):
    calls = []
    from app.services import notebook_service

    real = notebook_service.get_notebook_adapter

    def counting():
        adapter = real()
        launch = adapter.launch_session

        async def recorded(spec):
            calls.append(spec)
            return await launch(spec)

        adapter.launch_session = recorded
        return adapter

    monkeypatch.setattr(notebook_service, "get_notebook_adapter", counting)
    return calls


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_a_project_launch_records_the_project(
    client, session, comp_bio_token, comp_bio_user, seed_platform_config
):
    project = await _project(session, comp_bio_user.organization_id, "P")
    await session.commit()
    response = await client.post(
        NOTEBOOKS, json={"session_type": "jupyter", "project_id": project.id}, headers=_auth(comp_bio_token)
    )
    assert response.status_code == 200
    assert response.json()["project"]["id"] == project.id


async def test_an_experiment_launch_records_the_experiments_project(
    client, session, comp_bio_token, comp_bio_user, seed_platform_config
):
    project = await _project(session, comp_bio_user.organization_id, "P")
    exp = await _experiment(session, comp_bio_user.organization_id, "E", project.id)
    await session.commit()
    response = await client.post(
        NOTEBOOKS, json={"session_type": "jupyter", "experiment_id": exp.id}, headers=_auth(comp_bio_token)
    )
    assert response.status_code == 200
    row = (await session.execute(text("SELECT project_id, experiment_id FROM compute_sessions"))).one()
    assert tuple(row) == (project.id, exp.id)


async def test_a_standalone_experiment_stays_standalone(
    client, session, comp_bio_token, comp_bio_user, seed_platform_config
):
    exp = await _experiment(session, comp_bio_user.organization_id, "Solo")
    await session.commit()
    response = await client.post(
        NOTEBOOKS, json={"session_type": "jupyter", "experiment_id": exp.id}, headers=_auth(comp_bio_token)
    )
    assert response.status_code == 200
    assert response.json()["project"] is None


@pytest.mark.parametrize("field", ["project_id", "experiment_id"])
async def test_a_foreign_reference_is_refused_like_a_missing_one(
    client, session, comp_bio_token, comp_bio_user, seed_platform_config, provider_calls, field
):
    other = await _other_org(session)
    foreign = await (_project if field == "project_id" else _experiment)(session, other, "Theirs")
    await session.commit()
    body = {"session_type": "jupyter"}
    theirs = await client.post(NOTEBOOKS, json={**body, field: foreign.id}, headers=_auth(comp_bio_token))
    missing = await client.post(NOTEBOOKS, json={**body, field: 999999}, headers=_auth(comp_bio_token))
    assert theirs.status_code == 404 and missing.status_code == 404
    assert theirs.json() == missing.json()
    assert await _sessions(session) == 0 and provider_calls == []


@pytest.mark.parametrize("standalone", [False, True], ids=["other-project", "standalone"])
async def test_an_experiment_outside_the_given_project_is_refused(
    client, session, comp_bio_token, comp_bio_user, seed_platform_config, provider_calls, standalone
):
    org = comp_bio_user.organization_id
    chosen = await _project(session, org, "Chosen")
    elsewhere = None if standalone else (await _project(session, org, "Elsewhere")).id
    exp = await _experiment(session, org, "E", elsewhere)
    await session.commit()
    response = await client.post(
        NOTEBOOKS,
        json={"session_type": "jupyter", "project_id": chosen.id, "experiment_id": exp.id},
        headers=_auth(comp_bio_token),
    )
    assert response.status_code == 400
    assert await _sessions(session) == 0 and provider_calls == []


async def test_a_foreign_environment_version_is_refused(
    client, session, comp_bio_token, comp_bio_user, admin_user, seed_platform_config, provider_calls
):
    other = await _other_org(session)
    env = Environment(
        name="Theirs", organization_id=other, created_by_user_id=admin_user.id, environment_type="notebook"
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: t\n",
        image_uri="registry.example/their-image:1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.commit()
    response = await client.post(
        NOTEBOOKS,
        json={"session_type": "jupyter", "environment_version_id": version.id},
        headers=_auth(comp_bio_token),
    )
    assert response.status_code == 400 and response.json()["detail"] == "Environment version not found"
    assert "their-image" not in response.text
    assert await _sessions(session) == 0 and provider_calls == []


async def test_a_work_node_refuses_a_foreign_project_and_a_mismatched_experiment(
    client, session, comp_bio_token, comp_bio_user, seed_environment, seed_session_credentials, seed_platform_config
):
    org = comp_bio_user.organization_id
    other = await _other_org(session)
    theirs = await _project(session, other, "Theirs")
    mine = await _project(session, org, "Mine")
    exp = await _experiment(session, org, "E", (await _project(session, org, "Other")).id)
    await session.commit()
    base = {"environment_version_id": seed_environment["ready_version"].id, "machine_type": "n2-standard-4"}
    foreign = await client.post(WORK_NODES, json={**base, "project_id": theirs.id}, headers=_auth(comp_bio_token))
    mismatched = await client.post(
        WORK_NODES, json={**base, "project_id": mine.id, "experiment_id": exp.id}, headers=_auth(comp_bio_token)
    )
    assert foreign.status_code == 404 and mismatched.status_code == 400
    assert await _sessions(session) == 0
