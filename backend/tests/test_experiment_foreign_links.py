"""An experiment may only be filed under, and read through, its own organization.

``POST /api/experiments`` stored whatever ``project_id``, ``template_id`` and
``naming_profile_id`` it was given, and answered with the linked project's and
template's names, so another organization's id came straight back with its
name. A stored link of that kind then fed both project provenance readers,
which listed every experiment filed under the project (and every sample and
snapshot reached through it) whatever organization owned it.

Refusing new links does not close the readers, so each reader is checked here
against foreign rows seeded directly.
"""

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select

from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.experiment import Experiment
from app.models.experiment_template import ExperimentTemplate
from app.models.naming_profile import NamingProfile
from app.models.organization import Organization
from app.models.project import Project
from app.models.project_sample import ProjectSample
from app.models.sample import Sample
from app.models.user import User
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
    foreign_template = ExperimentTemplate(organization_id=other.id, name="THEIR SECRET TEMPLATE")
    foreign_profile = NamingProfile(
        organization_id=other.id, name="THEIR SECRET PROFILE", segments_json=[], created_by=stranger.id
    )
    session.add_all([project, foreign_project, foreign_template, foreign_profile])
    await session.flush()

    exp = Experiment(name="Our Exp", organization_id=org_id, project_id=project.id, owner_user_id=admin_user.id)
    # Theirs, filed under our project, as an unchecked create could store it.
    their_exp = Experiment(name="THEIR EXP", organization_id=other.id, project_id=project.id, owner_user_id=stranger.id)
    their_unfiled_exp = Experiment(name="THEIR OTHER EXP", organization_id=other.id, owner_user_id=stranger.id)
    session.add_all([exp, their_exp, their_unfiled_exp])
    await session.flush()

    our_sample = Sample(experiment_id=exp.id, external_id="OUR_SAMPLE")
    their_sample = Sample(experiment_id=their_exp.id, external_id="THEIR_SAMPLE")
    their_linked_sample = Sample(experiment_id=their_unfiled_exp.id, external_id="THEIR_LINKED_SAMPLE")
    session.add_all([our_sample, their_sample, their_linked_sample])
    await session.flush()

    # A foreign sample added to our project, and a foreign snapshot filed under it.
    await session.execute(
        insert(ProjectSample).values(
            project_id=project.id, sample_id=their_linked_sample.id, added_by_user_id=stranger.id
        )
    )
    session.add(
        AnalysisSnapshot(
            organization_id=other.id,
            project_id=project.id,
            user_id=stranger.id,
            label="THEIR SNAPSHOT",
            object_type="anndata",
        )
    )
    await session.commit()
    return {
        "org_id": org_id,
        "project": project,
        "foreign_project": foreign_project,
        "foreign_template": foreign_template,
        "foreign_profile": foreign_profile,
        "exp": exp,
        "our_sample": our_sample,
    }


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _experiments_named(session, name: str) -> int:
    return await session.scalar(select(func.count()).select_from(Experiment).where(Experiment.name == name))


class TestCreatingAnExperiment:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "key", "message"),
        [
            ("project_id", "foreign_project", "Project {} not found"),
            ("template_id", "foreign_template", "Experiment template {} not found"),
            ("naming_profile_id", "foreign_profile", "Naming profile {} not found"),
        ],
    )
    async def test_another_organizations_link_is_refused_and_nothing_is_stored(
        self, client, admin_token, session, world, field, key, message
    ):
        foreign_id = world[key].id
        r = await client.post("/api/experiments", json={"name": "Probe", field: foreign_id}, headers=_auth(admin_token))

        assert r.status_code == 404
        assert r.json()["detail"] == message.format(foreign_id)
        assert "THEIR" not in r.text
        assert await _experiments_named(session, "Probe") == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["project_id", "template_id", "naming_profile_id"])
    async def test_a_missing_link_is_refused_the_same_way(self, client, admin_token, session, world, field):
        r = await client.post("/api/experiments", json={"name": "Probe", field: 999999}, headers=_auth(admin_token))

        assert r.status_code == 404
        assert r.json()["detail"].endswith("999999 not found")
        assert await _experiments_named(session, "Probe") == 0

    @pytest.mark.asyncio
    async def test_our_own_project_is_linked_and_named(self, client, admin_token, world):
        r = await client.post(
            "/api/experiments",
            json={"name": "Ours", "project_id": world["project"].id},
            headers=_auth(admin_token),
        )

        assert r.status_code == 200
        assert r.json()["project"] == {"id": world["project"].id, "name": "Our Project"}


class TestProjectProvenanceDag:
    @pytest.mark.asyncio
    async def test_only_our_experiments_samples_and_snapshots_appear(self, client, admin_token, world):
        r = await client.get(f"/api/projects/{world['project'].id}/provenance", headers=_auth(admin_token))

        assert r.status_code == 200
        ids = {n["id"] for n in r.json()["nodes"]}
        assert f"experiment:{world['exp'].id}" in ids
        assert f"sample:{world['our_sample'].id}" in ids
        assert "THEIR" not in r.text


class TestProjectProvenanceReport:
    @pytest.mark.asyncio
    async def test_only_our_experiments_samples_and_snapshots_are_gathered(self, session, world):
        data = await ProvenanceDataGatherer.gather_project(session, world["project"].id, world["org_id"])

        assert "Our Exp" in repr(data)
        assert "OUR_SAMPLE" in repr(data)
        assert "THEIR" not in repr(data)

    @pytest.mark.asyncio
    async def test_the_report_endpoint_reveals_nothing_of_theirs(self, client, admin_token, world):
        r = await client.get(
            f"/api/projects/{world['project'].id}/provenance/report?format=json", headers=_auth(admin_token)
        )

        assert r.status_code == 200
        assert "THEIR" not in r.text
