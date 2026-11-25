"""08B: compute and pipeline lifecycle announcements follow the commit of the state they describe.

Work node launch/stop/heartbeat timeout, notebook idle auto-stops, pipeline OOM/completion/failure,
and run reviews each write a status (and usually an audit row) and then announce it. A subscriber on
its own session must find that state, so nothing is announced before the owning commit, and a
rollback or a failed commit announces nothing. The idle *warning* writes nothing; it stays an
immediate observation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.adapters.compute.kubernetes import _job_progress_from_dict, _job_status_from_dict
from app.models.audit_log import AuditLog
from app.models.notebook_session import ComputeSession, NotebookSession
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_review import PipelineRunReview
from app.services.event_types import (
    PIPELINE_COMPLETED,
    PIPELINE_FAILED,
    PIPELINE_OOM,
    PIPELINE_RUN_REVIEWED,
    SESSION_IDLE,
    WORK_NODE_HEARTBEAT_TIMEOUT,
    WORK_NODE_LAUNCHED,
    WORK_NODE_STOPPED,
)
from app.services.pipeline_monitor_service import PipelineMonitorService
from app.services.pipeline_review_service import PipelineReviewService
from app.services.work_node_service import WorkNodeService
from tests.support.committed_view import CommittedView
from tests.test_notebook_lifecycle_state import _seed as _seed_notebook
from tests.test_notebook_reconcile import FakeAdapter

pytestmark = pytest.mark.asyncio


async def _work_node(s, payload):
    node = await s.get(ComputeSession, payload["entity_id"])
    return None if node is None else node.status


async def _notebook(s, payload):
    ns = await s.get(NotebookSession, payload["entity_id"])
    return None if ns is None else ns.status


async def _run(s, payload):
    run = await s.get(PipelineRun, payload["entity_id"] if payload.get("entity_type") == "pipeline_run" else None)
    return None if run is None else (run.status, run.failure_reason)


async def _review(s, payload):
    return await s.get(PipelineRunReview, payload["entity_id"])


@pytest_asyncio.fixture
async def nodes():
    view = CommittedView([WORK_NODE_LAUNCHED, WORK_NODE_STOPPED, WORK_NODE_HEARTBEAT_TIMEOUT], read=_work_node)
    yield view
    view.close()


@pytest_asyncio.fixture
async def notebooks():
    view = CommittedView([SESSION_IDLE], read=_notebook)
    yield view
    view.close()


@pytest_asyncio.fixture
async def runs():
    view = CommittedView([PIPELINE_OOM, PIPELINE_COMPLETED, PIPELINE_FAILED], read=_run)
    yield view
    view.close()


@pytest_asyncio.fixture
async def reviews():
    view = CommittedView([PIPELINE_RUN_REVIEWED], read=_review)
    yield view
    view.close()


def _failing_commit(monkeypatch, session):
    """The next commit on ``session`` fails, as a lost connection or a constraint at commit would."""

    async def broken():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(session, "commit", broken)


# --- work nodes ---------------------------------------------------------------------------------


async def _launch(session, comp_bio_user, env, project):
    return await WorkNodeService.launch_work_node(
        session,
        user_id=comp_bio_user.id,
        org_id=comp_bio_user.organization_id,
        project_id=project.id,
        experiment_id=None,
        environment_version_id=env["ready_version"].id,
        machine_type="n2-standard-4",
        input_file_ids=None,
        github_repo_ids=None,
    )


@pytest_asyncio.fixture
async def ready_version(session, admin_user):
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    env = Environment(
        name="Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="work_node",
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: t\ndependencies:\n  - python=3.11\n",
        image_uri="projects/p/global/images/bioaf-worknode-t-v1-1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.commit()
    return {"ready_version": version}


@pytest_asyncio.fixture
async def launch_ready(session, admin_user, comp_bio_user, ready_version):
    from app.models.project import Project
    from app.services.session_credential_service import SessionCredentialService

    project = Project(name="P", organization_id=admin_user.organization_id, created_by_user_id=admin_user.id)
    session.add(project)
    await SessionCredentialService.create_or_update(
        session,
        user_id=comp_bio_user.id,
        org_id=comp_bio_user.organization_id,
        email=comp_bio_user.email,
        password="testpass",
    )
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('compute_deployed', 'true') ON CONFLICT (key) DO NOTHING"
        )
    )
    await session.commit()
    return ready_version, project


async def test_a_launch_is_announced_after_its_result_and_audit_commit(session, comp_bio_user, launch_ready, nodes):
    env, project = launch_ready
    node = await _launch(session, comp_bio_user, env, project)
    # launch_work_node commits `starting` part-way; the outcome and the audit are not committed yet.
    await nodes.settle()
    assert nodes.events == []

    await session.commit()
    await nodes.settle()

    [(payload, committed_status)] = nodes.seen(WORK_NODE_LAUNCHED)
    assert committed_status == node.status
    assert (payload["org_id"], payload["entity_id"], payload["target_user_id"]) == (
        comp_bio_user.organization_id,
        node.id,
        comp_bio_user.id,
    )
    audits = (
        await session.execute(select(AuditLog).where(AuditLog.entity_type == "work_node", AuditLog.action == "launch"))
    ).scalars()
    assert [a.entity_id for a in audits] == [node.id]


async def test_a_launch_rolled_back_after_its_intermediate_commit_is_not_announced(
    session, comp_bio_user, launch_ready, nodes
):
    env, project = launch_ready
    await _launch(session, comp_bio_user, env, project)
    await session.rollback()
    await session.execute(text("SELECT 1"))
    await session.commit()
    await nodes.settle()

    assert nodes.events == []


async def test_a_stop_is_announced_only_with_its_committed_stopped_state(session, comp_bio_user, launch_ready, nodes):
    env, project = launch_ready
    node = await _launch(session, comp_bio_user, env, project)
    await session.commit()
    await nodes.settle()

    await WorkNodeService.stop_work_node(session, node.id, comp_bio_user.id, org_id=comp_bio_user.organization_id)
    await nodes.settle()
    assert nodes.seen(WORK_NODE_STOPPED) == []

    await session.commit()
    await nodes.settle()
    [(payload, committed_status)] = nodes.seen(WORK_NODE_STOPPED)
    assert committed_status == "stopped"
    assert payload["entity_id"] == node.id and payload["org_id"] == comp_bio_user.organization_id


async def test_a_rolled_back_stop_is_not_announced(session, comp_bio_user, launch_ready, nodes):
    env, project = launch_ready
    node = await _launch(session, comp_bio_user, env, project)
    await session.commit()

    await WorkNodeService.stop_work_node(session, node.id, comp_bio_user.id, org_id=comp_bio_user.organization_id)
    await session.rollback()
    await nodes.settle()

    assert nodes.seen(WORK_NODE_STOPPED) == []


async def _stale_node(session, user, env, **extra) -> ComputeSession:
    node = ComputeSession(
        user_id=user.id,
        organization_id=user.organization_id,
        session_type="ssh",
        resource_profile="custom",
        cpu_cores=4,
        memory_gb=16,
        status="running",
        heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=25),
        heartbeat_token="t",
        environment_version_id=env["ready_version"].id,
        machine_type="n2-standard-4",
        **extra,
    )
    session.add(node)
    await session.commit()
    return node


async def test_a_silent_node_is_not_stopped_or_announced(session, admin_user, ready_version, nodes):
    """Owner-approved change (2026-11-25): missing heartbeats never stop a work node, so the
    heartbeat-timeout announcement is not sent."""
    node = await _stale_node(session, admin_user, ready_version)

    await WorkNodeService.check_heartbeat_timeouts(session, idle_timeout_hours=24)
    await nodes.settle()

    assert nodes.seen(WORK_NODE_HEARTBEAT_TIMEOUT) == []
    await session.refresh(node)
    assert node.status == "running"


# --- notebook idle auto-stop --------------------------------------------------------------------


@pytest.fixture
def fake_notebooks(monkeypatch):
    from app.adapters import registry
    from app.services import notebook_service

    adapter = FakeAdapter()
    monkeypatch.setattr(registry, "get_notebook_adapter", lambda: adapter)
    monkeypatch.setattr(notebook_service, "get_notebook_adapter", lambda: adapter)
    return adapter


async def test_an_idle_auto_stop_is_announced_once_committed(session, admin_user, fake_notebooks, notebooks):
    from app.services.notebook_service import NotebookService

    ns = await _seed_notebook(session, admin_user, status="idle")
    ns.idle_since = datetime.now(timezone.utc) - timedelta(hours=5)
    await session.commit()

    await NotebookService.check_idle_sessions(session, idle_timeout_hours=4)
    await notebooks.settle()

    [(payload, committed_status)] = notebooks.seen(SESSION_IDLE)
    assert committed_status == "stopped"
    assert (payload["org_id"], payload["entity_id"]) == (admin_user.organization_id, ns.id)


async def test_an_idle_auto_stop_whose_commit_fails_is_not_announced(
    session, admin_user, fake_notebooks, notebooks, monkeypatch
):
    from app.services.notebook_service import NotebookService

    ns = await _seed_notebook(session, admin_user, status="idle")
    ns.idle_since = datetime.now(timezone.utc) - timedelta(hours=5)
    await session.commit()
    _failing_commit(monkeypatch, session)

    await NotebookService.check_idle_sessions(session, idle_timeout_hours=4)
    monkeypatch.undo()
    await session.rollback()
    await notebooks.settle()

    assert notebooks.events == []


async def test_the_monitor_announces_an_idle_stop_only_when_it_commits_but_warns_immediately(
    session, admin_user, fake_notebooks, notebooks, monkeypatch
):
    """The idle warning is an observation (nothing is written), so it is not tied to the commit; the
    auto-stop is a state change, so it is."""
    from app.services.session_monitor import SessionMonitorService

    stale = await _seed_notebook(session, admin_user, status="running")
    stale.last_activity_at = datetime.now(timezone.utc) - timedelta(hours=5)
    nearly = await _seed_notebook(session, admin_user, status="running")
    nearly.last_activity_at = datetime.now(timezone.utc) - timedelta(hours=3, minutes=50)
    await session.commit()
    nearly_id = nearly.id
    _failing_commit(monkeypatch, session)

    await SessionMonitorService.poll_notebook_sessions(session, idle_timeout_hours=4, warning_minutes=15)
    monkeypatch.undo()
    await session.rollback()
    await notebooks.settle()

    assert [p["entity_id"] for p in notebooks.payloads(SESSION_IDLE)] == [nearly_id]
    assert "will be stopped soon" in notebooks.payloads(SESSION_IDLE)[0]["title"]


async def test_the_monitor_announces_a_committed_idle_stop(session, admin_user, fake_notebooks, notebooks):
    from app.services.session_monitor import SessionMonitorService

    stale = await _seed_notebook(session, admin_user, status="running")
    stale.last_activity_at = datetime.now(timezone.utc) - timedelta(hours=5)
    await session.commit()

    await SessionMonitorService.poll_notebook_sessions(session, idle_timeout_hours=4)
    await notebooks.settle()

    [(payload, committed_status)] = notebooks.seen(SESSION_IDLE)
    assert payload["entity_id"] == stale.id and committed_status == "stopped"


# --- pipeline monitor ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def k8s_run(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(organization_id=admin_user.organization_id, name="E", owner_user_id=admin_user.id)
    session.add(exp)
    await session.flush()
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        status="running",
        k8s_job_name="bioaf-pipeline-x",
        compute_job_ref="bioaf-pipeline-x",
        k8s_namespace="bioaf-pipelines",
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.commit()
    return run


def _compute(status: str, *, oom: bool = False) -> AsyncMock:
    compute = AsyncMock()
    reasons = [{"container": "pipeline", "exit_code": 137, "reason": "OOMKilled"}] if oom else []
    compute.get_job_status.return_value = _job_status_from_dict(
        {"status": status, "pod_name": "p", "node_name": "n", "termination_reasons": reasons}
    )
    compute.get_job_progress.return_value = _job_progress_from_dict({"percent_complete": 100.0, "processes": []})
    compute.get_job_logs.return_value = "log"
    return compute


def _adapters(compute):
    storage = AsyncMock()
    storage.collect_outputs.return_value = []
    return (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=storage),
    )


@pytest.mark.parametrize(
    "status, oom, expected",
    [
        ("completed", False, {PIPELINE_COMPLETED: ("completed", None)}),
        ("failed", False, {PIPELINE_FAILED: ("failed", "task_error")}),
        ("failed", True, {PIPELINE_OOM: ("failed", "oom"), PIPELINE_FAILED: ("failed", "oom")}),
    ],
    ids=["completed", "failed", "oom"],
)
async def test_run_outcomes_are_announced_with_their_committed_status(
    session, admin_user, k8s_run, runs, status, oom, expected
):
    compute_patch, storage_patch = _adapters(_compute(status, oom=oom))
    with compute_patch, storage_patch:
        await PipelineMonitorService.sync_run_statuses(session)
    await runs.settle()

    observed = {kind: committed for kind, _payload, committed in runs.events}
    assert observed == expected
    assert {p["org_id"] for _, p, _ in runs.events} == {admin_user.organization_id}


@pytest.mark.parametrize("status, oom", [("completed", False), ("failed", True)], ids=["completed", "oom"])
async def test_run_outcomes_whose_commit_fails_are_not_announced(
    session, admin_user, k8s_run, runs, monkeypatch, status, oom
):
    run_id = k8s_run.id
    compute_patch, storage_patch = _adapters(_compute(status, oom=oom))
    _failing_commit(monkeypatch, session)
    with compute_patch, storage_patch:
        await PipelineMonitorService.sync_run_statuses(session)
    monkeypatch.undo()
    await session.rollback()
    await runs.settle()

    assert runs.events == []
    assert (await session.get(PipelineRun, run_id)).status == "running"


# --- run reviews --------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def experiment_and_run(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(organization_id=admin_user.organization_id, name="E", status="pipeline_complete")
    session.add(exp)
    await session.flush()
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_name="nf-core/scrnaseq",
        status="completed",
    )
    session.add(run)
    await session.commit()
    return {"experiment_id": exp.id, "run_id": run.id}


async def test_a_review_is_announced_once_committed(client, comp_bio_token, experiment_and_run, reviews):
    resp = await client.post(
        f"/api/pipeline-runs/{experiment_and_run['run_id']}/reviews",
        json={"verdict": "approved", "notes": "ok"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 201
    await reviews.settle()

    [(payload, committed)] = reviews.seen(PIPELINE_RUN_REVIEWED)
    assert committed is not None and committed.id == resp.json()["id"]
    assert payload["metadata"] == {
        "pipeline_run_id": experiment_and_run["run_id"],
        "experiment_id": experiment_and_run["experiment_id"],
        "verdict": "approved",
    }


async def test_a_rolled_back_review_is_not_announced(session, admin_user, experiment_and_run, reviews):
    run = await session.get(PipelineRun, experiment_and_run["run_id"])
    await PipelineReviewService.create_review(
        session,
        run.id,
        run.organization_id,
        admin_user.id,
        "approved",
        None,
        None,
        None,
    )
    await reviews.settle()
    assert reviews.events == []

    await session.rollback()
    await reviews.settle()
    assert reviews.events == []
