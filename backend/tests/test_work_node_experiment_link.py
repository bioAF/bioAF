"""Plan 03.5E: a Work Node shows the Experiment it was launched for.

The launch already records ``experiment_id``; these tests prove the Experiment reaches every response a
scientist reads (launch, list, detail, Stop), that the outputs saved at Stop carry the same Experiment, and
that no association is guessed for a node that recorded none.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import event, select

from app.adapters.models import StoredObject, TerminationResult
from app.models.experiment import Experiment
from app.models.file import File
from app.models.notebook_session import ComputeSession
from app.services.event_bus import event_bus
from tests import test_work_nodes as _work_node_suite

seed_environment = _work_node_suite.seed_environment
seed_platform_config = _work_node_suite.seed_platform_config
seed_project = _work_node_suite.seed_project
seed_session_credentials = _work_node_suite.seed_session_credentials


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def project_experiment(session, comp_bio_user, seed_project):
    exp = Experiment(
        name="Organoid timecourse", organization_id=comp_bio_user.organization_id, project_id=seed_project.id
    )
    session.add(exp)
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def standalone_experiment(session, comp_bio_user):
    exp = Experiment(name="Standalone screen", organization_id=comp_bio_user.organization_id)
    session.add(exp)
    await session.commit()
    return exp


async def _launch(client, token, seed_environment, **scope):
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            **scope,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _detail(client, token, node_id):
    response = await client.get(f"/api/v1/work-nodes/sessions/{node_id}", headers=_bearer(token))
    assert response.status_code == 200
    return response.json()


async def _listed(client, token, node_id):
    response = await client.get("/api/v1/work-nodes/sessions", headers=_bearer(token))
    assert response.status_code == 200
    return next(s for s in response.json()["sessions"] if s["id"] == node_id)


@pytest.mark.asyncio
async def test_an_experiment_launch_shows_the_experiment_and_its_project_everywhere(
    client,
    comp_bio_token,
    seed_environment,
    seed_project,
    project_experiment,
    seed_session_credentials,
    seed_platform_config,
):
    launched = await _launch(
        client, comp_bio_token, seed_environment, project_id=seed_project.id, experiment_id=project_experiment.id
    )
    for body in (
        launched,
        await _detail(client, comp_bio_token, launched["id"]),
        await _listed(client, comp_bio_token, launched["id"]),
    ):
        assert body["experiment_id"] == project_experiment.id
        assert body["experiment"] == {"id": project_experiment.id, "name": "Organoid timecourse"}
        assert body["project"] == {"id": seed_project.id, "name": "Test Project"}


@pytest.mark.asyncio
async def test_an_experiment_launch_without_a_project_choice_records_the_experiments_project(
    client,
    comp_bio_token,
    seed_environment,
    seed_project,
    project_experiment,
    seed_session_credentials,
    seed_platform_config,
):
    launched = await _launch(client, comp_bio_token, seed_environment, experiment_id=project_experiment.id)
    body = await _detail(client, comp_bio_token, launched["id"])
    assert body["experiment"] == {"id": project_experiment.id, "name": "Organoid timecourse"}
    assert body["project_id"] == seed_project.id


@pytest.mark.asyncio
async def test_a_standalone_experiment_is_shown_with_no_project(
    client,
    comp_bio_token,
    seed_environment,
    standalone_experiment,
    seed_session_credentials,
    seed_platform_config,
):
    launched = await _launch(client, comp_bio_token, seed_environment, experiment_id=standalone_experiment.id)
    for body in (
        launched,
        await _detail(client, comp_bio_token, launched["id"]),
        await _listed(client, comp_bio_token, launched["id"]),
    ):
        assert body["experiment"] == {"id": standalone_experiment.id, "name": "Standalone screen"}
        assert body["project_id"] is None
        assert body["project"] is None


@pytest.mark.asyncio
async def test_a_project_only_node_shows_no_experiment(
    client,
    comp_bio_token,
    seed_environment,
    seed_project,
    project_experiment,
    seed_session_credentials,
    seed_platform_config,
):
    """The Project's Experiment is never guessed for a node launched for the Project alone."""
    launched = await _launch(client, comp_bio_token, seed_environment, project_id=seed_project.id)
    body = await _detail(client, comp_bio_token, launched["id"])
    assert body["experiment_id"] is None
    assert body["experiment"] is None
    assert body["project"] == {"id": seed_project.id, "name": "Test Project"}


@pytest.mark.asyncio
async def test_a_legacy_unlinked_node_stays_unlinked(client, session, comp_bio_token, comp_bio_user, seed_project):
    node = ComputeSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        machine_type="n2-standard-4",
        cpu_cores=4,
        memory_gb=16,
        status="stopped",
    )
    session.add(node)
    await session.commit()
    for body in (await _detail(client, comp_bio_token, node.id), await _listed(client, comp_bio_token, node.id)):
        assert (body["experiment_id"], body["experiment"], body["project_id"], body["project"]) == (
            None,
            None,
            None,
            None,
        )


@pytest.mark.asyncio
async def test_another_organizations_experiment_cannot_be_linked(
    client, session, comp_bio_token, seed_environment, seed_session_credentials, seed_platform_config
):
    from app.models.organization import Organization

    other_org = Organization(name="Elsewhere")
    session.add(other_org)
    await session.flush()
    foreign = Experiment(name="Theirs", organization_id=other_org.id)
    session.add(foreign)
    await session.commit()
    response = await client.post(
        "/api/v1/work-nodes/sessions",
        json={
            "experiment_id": foreign.id,
            "environment_version_id": seed_environment["ready_version"].id,
            "machine_type": "n2-standard-4",
        },
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 404
    assert "Theirs" not in response.text
    assert (await session.scalar(select(ComputeSession.id).where(ComputeSession.experiment_id == foreign.id))) is None


@pytest.mark.asyncio
async def test_stop_keeps_the_experiment_and_saved_outputs_carry_it(
    client, session, comp_bio_token, comp_bio_user, seed_project, project_experiment
):
    node = ComputeSession(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        project_id=seed_project.id,
        experiment_id=project_experiment.id,
        session_type="ssh",
        resource_profile="custom",
        machine_type="n2-standard-4",
        cpu_cores=4,
        memory_gb=16,
        status="running",
        gce_instance_name="bioaf-worknode-1",
        gce_zone="us-central1-b",
        provider_instance_id="555",
        launch_attempt_id="attempt-1",
        sync_private_key="sync-private",
        host_public_key="ssh-ed25519 AAAAhost",
        node_trust_version=1,
    )
    session.add(node)
    await session.commit()
    node_id, experiment_id, project_id = node.id, project_experiment.id, seed_project.id

    adapter = MagicMock(name="work node adapter")
    adapter.terminate_vm = AsyncMock(
        return_value=TerminationResult(
            output_files=[
                StoredObject(filename="counts.csv", storage_uri="gs://w/sessions/1/outputs/counts.csv", size_bytes=3)
            ],
            output_prefix="gs://w/sessions/1/outputs/",
        )
    )
    with (
        patch("app.services.work_node_service.get_work_node_adapter", return_value=adapter),
        patch.object(event_bus, "emit", AsyncMock()),
    ):
        response = await client.post(f"/api/v1/work-nodes/sessions/{node.id}/stop", headers=_bearer(comp_bio_token))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stopped"
    assert body["experiment"] == {"id": project_experiment.id, "name": "Organoid timecourse"}

    session.expire_all()
    saved = (
        await session.execute(
            select(File.filename, File.experiment_id, File.project_id).where(File.source_notebook_session_id == node_id)
        )
    ).all()
    assert [tuple(r) for r in saved] == [("counts.csv", experiment_id, project_id)]


@pytest.mark.asyncio
async def test_listing_loads_experiments_without_a_query_per_node(
    client, session, comp_bio_token, comp_bio_user, seed_project
):
    for i in range(4):
        exp = Experiment(name=f"E{i}", organization_id=comp_bio_user.organization_id, project_id=seed_project.id)
        session.add(exp)
        await session.flush()
        session.add(
            ComputeSession(
                user_id=comp_bio_user.id,
                organization_id=comp_bio_user.organization_id,
                project_id=seed_project.id,
                experiment_id=exp.id,
                session_type="ssh",
                resource_profile="custom",
                cpu_cores=4,
                memory_gb=16,
                status="running",
            )
        )
    await session.commit()

    statements: list[str] = []

    def _count(conn, cursor, statement, *args):
        if "experiments" in statement:
            statements.append(statement)

    engine = session.bind.sync_engine if hasattr(session.bind, "sync_engine") else session.bind
    event.listen(engine, "before_cursor_execute", _count)
    try:
        response = await client.get("/api/v1/work-nodes/sessions", headers=_bearer(comp_bio_token))
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert response.status_code == 200
    names = sorted(s["experiment"]["name"] for s in response.json()["sessions"])
    assert names == ["E0", "E1", "E2", "E3"]
    assert len(statements) == 1
