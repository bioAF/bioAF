"""Readers that must not follow a stored link into another organization.

Launch used to store ``project_id`` and ``resume_from_run_id`` without checking
who owned them, so rows already exist that point across organizations. Refusing
new links does not close the readers: each one is checked here against such a
row, seeded directly.

- Project provenance (the DAG and the report gatherer) listed every run filed
  under the project, or under one of its experiments, whatever organization
  submitted it, along with that run's references, users and audit entries.
- A run report resolved its resume parent by bare id.
- The QC dashboard context named whatever project the run pointed at.
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import insert

from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.qc_dashboard import QCDashboard
from app.models.reference_dataset import ReferenceDataset, pipeline_run_references
from app.models.user import User
from app.services.audit_service import log_action
from app.services.auth_service import AuthService
from app.services.provenance.data_gatherer import ProvenanceDataGatherer


@pytest_asyncio.fixture
async def world(session, admin_user):
    org_id = admin_user.organization_id
    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    stranger = User(
        email="stranger@test.com",
        name="THEIR USER",
        password_hash=AuthService.hash_password("strangerpass1"),
        role_id=admin_user.role_id,
        organization_id=other.id,
        status="active",
    )
    session.add(stranger)
    await session.flush()

    project = Project(organization_id=org_id, name="Our Project")
    foreign_project = Project(organization_id=other.id, name="THEIR SECRET PROJECT")
    session.add_all([project, foreign_project])
    await session.flush()
    exp = Experiment(name="Our Exp", organization_id=org_id, project_id=project.id, owner_user_id=admin_user.id)
    foreign_exp = Experiment(name="Their Exp", organization_id=other.id, owner_user_id=stranger.id)
    session.add_all([exp, foreign_exp])
    await session.flush()

    their_ref = ReferenceDataset(
        organization_id=other.id,
        name="THEIR_REFERENCE",
        category="genome",
        scope="internal",
        version="1",
        gcs_prefix="gs://theirs/ref",
    )
    session.add(their_ref)
    await session.flush()

    def run(org, user, **kw):
        return PipelineRun(organization_id=org, submitted_by_user_id=user.id, status="completed", work_dir="/w", **kw)

    our_run = run(org_id, admin_user, experiment_id=exp.id, project_id=project.id, pipeline_name="OUR PIPELINE")
    our_exp_run = run(org_id, admin_user, experiment_id=exp.id, pipeline_name="OUR EXP PIPELINE")
    # Theirs, filed under our project directly, and under our experiment with no project.
    their_project_run = run(
        other.id, stranger, experiment_id=foreign_exp.id, project_id=project.id, pipeline_name="THEIR PIPELINE"
    )
    their_exp_run = run(other.id, stranger, experiment_id=exp.id, pipeline_name="THEIR EXP PIPELINE")
    their_parent = run(other.id, stranger, experiment_id=foreign_exp.id, pipeline_name="THEIR PARENT")
    session.add_all([our_run, our_exp_run, their_project_run, their_exp_run, their_parent])
    await session.flush()

    # Ours, resumed from theirs; ours, filed under their project.
    our_resumed = run(
        org_id, admin_user, experiment_id=exp.id, pipeline_name="OUR RESUMED", resume_from_run_id=their_parent.id
    )
    our_run_in_their_project = run(
        org_id, admin_user, experiment_id=exp.id, project_id=foreign_project.id, pipeline_name="OUR MISFILED"
    )
    session.add_all([our_resumed, our_run_in_their_project])
    await session.flush()

    for r in (their_project_run, their_exp_run):
        await session.execute(
            insert(pipeline_run_references).values(pipeline_run_id=r.id, reference_dataset_id=their_ref.id)
        )
        await log_action(session, stranger.id, "pipeline_run", r.id, "launch", details={"secret": "THEIR AUDIT"})
    await session.commit()
    return {
        "org_id": org_id,
        "other": other,
        "project": project,
        "foreign_project": foreign_project,
        "exp": exp,
        "our_run": our_run,
        "our_exp_run": our_exp_run,
        "their_project_run": their_project_run,
        "their_exp_run": their_exp_run,
        "their_parent": their_parent,
        "our_resumed": our_resumed,
        "our_run_in_their_project": our_run_in_their_project,
    }


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestProjectProvenanceDag:
    @pytest.mark.asyncio
    async def test_only_our_runs_appear_under_our_project(self, client, admin_token, world):
        r = await client.get(f"/api/projects/{world['project'].id}/provenance", headers=_auth(admin_token))

        assert r.status_code == 200
        run_ids = {n["id"] for n in r.json()["nodes"] if n["type"] == "pipeline_run"}
        assert f"pipeline_run:{world['our_run'].id}" in run_ids
        assert f"pipeline_run:{world['our_exp_run'].id}" in run_ids
        assert f"pipeline_run:{world['their_project_run'].id}" not in run_ids
        assert f"pipeline_run:{world['their_exp_run'].id}" not in run_ids
        assert "THEIR" not in r.text


class TestProjectProvenanceReport:
    @pytest.mark.asyncio
    async def test_foreign_runs_and_what_hangs_off_them_are_excluded(self, session, world):
        data = await ProvenanceDataGatherer.gather_project(session, world["project"].id, world["org_id"])

        run_ids = {r["id"] for r in data.pipeline_runs}
        assert {world["our_run"].id, world["our_exp_run"].id} <= run_ids
        assert world["their_project_run"].id not in run_ids
        assert world["their_exp_run"].id not in run_ids
        assert data.references == []
        assert "THEIR" not in repr(data)

    @pytest.mark.asyncio
    async def test_the_report_endpoint_reveals_nothing_of_theirs(self, client, admin_token, world):
        r = await client.get(
            f"/api/projects/{world['project'].id}/provenance/report?format=json", headers=_auth(admin_token)
        )

        assert r.status_code == 200
        assert "THEIR" not in r.text


class TestResumeParent:
    @pytest.mark.asyncio
    async def test_a_foreign_parent_resolves_to_nothing(self, session, world):
        data = await ProvenanceDataGatherer.gather_pipeline_run(session, world["our_resumed"].id, world["org_id"])

        assert data.run["id"] == world["our_resumed"].id
        assert data.resume_from is None
        assert "THEIR PARENT" not in repr(data)

    @pytest.mark.asyncio
    async def test_our_own_parent_still_resolves(self, session, world):
        world["our_resumed"].resume_from_run_id = world["our_run"].id
        await session.commit()

        data = await ProvenanceDataGatherer.gather_pipeline_run(session, world["our_resumed"].id, world["org_id"])

        assert data.resume_from == {"id": world["our_run"].id, "pipeline_name": "OUR PIPELINE", "status": "completed"}


class TestQcDashboardContext:
    @pytest_asyncio.fixture
    async def dashboards(self, session, world):
        def dash(run):
            return QCDashboard(
                organization_id=world["org_id"],
                pipeline_run_id=run.id,
                experiment_id=world["exp"].id,
                metrics_json={"quality_rating": "good"},
                plots_json=[],
                status="ready",
            )

        misfiled, owned, unfiled = (
            dash(world["our_run_in_their_project"]),
            dash(world["our_run"]),
            dash(world["our_exp_run"]),
        )
        session.add_all([misfiled, owned, unfiled])
        await session.commit()
        return {"misfiled": misfiled, "owned": owned, "unfiled": unfiled}

    @pytest.mark.asyncio
    async def test_the_list_keeps_the_dashboard_and_drops_their_project_name(
        self, client, admin_token, world, dashboards
    ):
        r = await client.get("/api/qc-dashboards", headers=_auth(admin_token))

        assert r.status_code == 200
        by_id = {d["id"]: d for d in r.json()}
        assert by_id[dashboards["misfiled"].id]["project_name"] is None
        assert by_id[dashboards["owned"].id]["project_name"] == "Our Project"
        assert by_id[dashboards["unfiled"].id]["project_name"] is None
        assert "THEIR SECRET PROJECT" not in r.text

    @pytest.mark.asyncio
    async def test_detail_and_by_run_drop_their_project_name(self, client, admin_token, world, dashboards):
        detail = await client.get(f"/api/qc-dashboards/{dashboards['misfiled'].id}", headers=_auth(admin_token))
        by_run = await client.get(
            f"/api/qc-dashboards/by-run/{world['our_run_in_their_project'].id}", headers=_auth(admin_token)
        )

        for r in (detail, by_run):
            assert r.status_code == 200
            assert r.json()["project_name"] is None
            assert r.json()["experiment_name"] == "Our Exp"
            assert "THEIR SECRET PROJECT" not in r.text

    @pytest.mark.asyncio
    async def test_our_project_name_still_shows_in_detail(self, client, admin_token, dashboards):
        r = await client.get(f"/api/qc-dashboards/{dashboards['owned'].id}", headers=_auth(admin_token))

        assert r.json()["project_name"] == "Our Project"

    @pytest.mark.asyncio
    async def test_a_generated_dashboard_drops_their_project_name(self, client, admin_token, world, dashboards):
        with (
            patch("app.services.component_service.ComponentService.is_enabled", new=AsyncMock(return_value=True)),
            patch(
                "app.api.qc_dashboards.QCDashboardService.generate_qc_dashboard",
                new=AsyncMock(return_value=dashboards["misfiled"]),
            ),
        ):
            r = await client.post(
                f"/api/qc-dashboards/generate/{world['our_run_in_their_project'].id}", headers=_auth(admin_token)
            )

        assert r.status_code == 200
        assert r.json()["project_name"] is None
        assert "THEIR SECRET PROJECT" not in r.text
