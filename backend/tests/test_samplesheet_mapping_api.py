"""Reaching a saved samplesheet design from the launch flow.

The service that stores a design already exists and is pinned by
``test_samplesheet_mappings.py``. Nothing called it, so no launch has ever been
offered one back. These tests pin the surface that closes that gap, and the two
rules that keep it from becoming the hazard it exists to remove.

**Promotion follows the blast radius.** Anyone who can launch in an experiment
authors its mapping; promoting to the project follows project access; promoting
to the organization requires an admin, because that is the only rung where one
person's decision reaches people who did not choose it.

**A prefill is an offer, never an application.** The preflight reports what a
saved design would contribute and which selected samples it does not name, and
the sheet it returns still contains only what the caller actually sent. A design
that is right for six samples may be wrong for twelve, and a prefilled value
looks plausible precisely because it was correct last time.
"""

import json
from pathlib import Path

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.project import Project
from app.models.role import Role, RolePermission
from app.models.sample import Sample
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"
KEY = "nf-core/mag"


def _schema(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _headers(user, role_name: str) -> dict:
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def world(session):
    org = Organization(name="MappingApiOrg", setup_complete=True)
    other_org = Organization(name="StrangerOrg", setup_complete=True)
    session.add_all([org, other_org])
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)

    # A role that can launch but holds no project rights, which is the shape the
    # project rung has to refuse. No built-in role has it: comp_bio can launch
    # AND edit projects, and bench can do neither.
    launcher_role = Role(name="launcher", description="Launch only", organization_id=org.id, is_system=False)
    session.add(launcher_role)
    await session.flush()
    session.add_all(
        [
            RolePermission(role_id=launcher_role.id, resource="pipelines", action="launch"),
            RolePermission(role_id=launcher_role.id, resource="pipelines", action="view"),
        ]
    )

    def _user(email, role_id):
        return User(
            email=email,
            password_hash=AuthService.hash_password("testpass123"),
            role_id=role_id,
            organization_id=org.id,
            status="active",
        )

    admin = _user("admin@mapping.test", roles["admin"])
    comp_bio = _user("compbio@mapping.test", roles["comp_bio"])
    launcher = _user("launcher@mapping.test", launcher_role.id)
    session.add_all([admin, comp_bio, launcher])
    await session.flush()

    project = Project(name="Gut Study", organization_id=org.id, owner_user_id=admin.id)
    session.add(project)
    await session.flush()

    exp = Experiment(
        name="Run 1",
        organization_id=org.id,
        project_id=project.id,
        status="fastq_uploaded",
        owner_user_id=admin.id,
    )
    stranger_exp = Experiment(
        name="Not Yours", organization_id=other_org.id, status="fastq_uploaded", owner_user_id=admin.id
    )
    session.add_all([exp, stranger_exp])
    await session.flush()

    session.add(
        PipelineCatalogEntry(
            organization_id=org.id,
            pipeline_key=KEY,
            name=KEY,
            source_type="nf-core",
            source_url=f"https://github.com/{KEY}",
            version="5.5.0",
            default_params_json={},
            input_schema_json=_schema("mag"),
            enabled=True,
        )
    )

    first = Sample(experiment_id=exp.id, external_id="SAMPLE-1", organism="Homo sapiens")
    second = Sample(experiment_id=exp.id, external_id="SAMPLE-2", organism="Homo sapiens")
    session.add_all([first, second])
    await session.flush()
    await session.commit()

    return {
        "org": org,
        "admin": admin,
        "comp_bio": comp_bio,
        "launcher": launcher,
        "project": project,
        "exp": exp,
        "stranger_exp": stranger_exp,
        "first": first,
        "second": second,
    }


async def _save(client, headers, **body):
    payload = {"pipeline_key": KEY, "scope": "experiment"}
    payload.update(body)
    return await client.post("/api/samplesheet-mappings", json=payload, headers=headers)


async def _resolve(client, headers, experiment_id):
    return await client.get(
        "/api/samplesheet-mappings",
        params={"pipeline_key": KEY, "experiment_id": experiment_id},
        headers=headers,
    )


class TestADesignIsSavedAndOfferedBack:
    @pytest.mark.asyncio
    async def test_a_launcher_saves_and_reads_back_an_experiment_design(self, client, world):
        headers = _headers(world["launcher"], "launcher")
        saved = await _save(
            client,
            headers,
            experiment_id=world["exp"].id,
            values={str(world["first"].id): {"group": "gut"}},
        )
        assert saved.status_code == 200

        r = await _resolve(client, headers, world["exp"].id)
        assert r.status_code == 200
        body = r.json()
        assert body["scope"] == "experiment"
        assert body["values"] == {str(world["first"].id): {"group": "gut"}}

    @pytest.mark.asyncio
    async def test_nothing_saved_resolves_to_no_scope(self, client, world):
        r = await _resolve(client, _headers(world["admin"], "admin"), world["exp"].id)
        assert r.status_code == 200
        assert r.json()["scope"] is None
        assert r.json()["values"] == {}

    @pytest.mark.asyncio
    async def test_the_experiment_design_wins_over_the_organization_one(self, client, world):
        admin = _headers(world["admin"], "admin")
        await _save(client, admin, scope="organization", values={str(world["first"].id): {"group": "everywhere"}})
        await _save(client, admin, experiment_id=world["exp"].id, values={str(world["first"].id): {"group": "here"}})

        body = (await _resolve(client, admin, world["exp"].id)).json()
        assert body["scope"] == "experiment"
        assert body["values"][str(world["first"].id)]["group"] == "here"

    @pytest.mark.asyncio
    async def test_saving_again_edits_the_one_mapping_rather_than_adding_a_rival(self, client, world):
        headers = _headers(world["admin"], "admin")
        await _save(client, headers, experiment_id=world["exp"].id, values={str(world["first"].id): {"group": "gut"}})
        await _save(client, headers, experiment_id=world["exp"].id, values={str(world["first"].id): {"group": "skin"}})

        body = (await _resolve(client, headers, world["exp"].id)).json()
        assert body["values"] == {str(world["first"].id): {"group": "skin"}}


class TestPromotionFollowsTheBlastRadius:
    @pytest.mark.asyncio
    async def test_promoting_to_the_organization_requires_an_admin(self, client, world):
        r = await _save(
            client,
            _headers(world["comp_bio"], "comp_bio"),
            scope="organization",
            values={str(world["first"].id): {"group": "gut"}},
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_an_admin_may_promote_to_the_organization(self, client, world):
        r = await _save(
            client,
            _headers(world["admin"], "admin"),
            scope="organization",
            values={str(world["first"].id): {"group": "gut"}},
        )
        assert r.status_code == 200
        assert r.json()["scope"] == "organization"

    @pytest.mark.asyncio
    async def test_promoting_to_a_project_requires_project_access(self, client, world):
        r = await _save(
            client,
            _headers(world["launcher"], "launcher"),
            scope="project",
            project_id=world["project"].id,
            values={str(world["first"].id): {"group": "gut"}},
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_project_access_is_enough_for_the_project_rung(self, client, world):
        r = await _save(
            client,
            _headers(world["comp_bio"], "comp_bio"),
            scope="project",
            project_id=world["project"].id,
            values={str(world["first"].id): {"group": "gut"}},
        )
        assert r.status_code == 200
        assert r.json()["scope"] == "project"

    @pytest.mark.asyncio
    async def test_another_organizations_experiment_is_not_found(self, client, world):
        r = await _save(
            client,
            _headers(world["admin"], "admin"),
            experiment_id=world["stranger_exp"].id,
            values={str(world["first"].id): {"group": "gut"}},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_an_experiment_scoped_save_needs_an_experiment(self, client, world):
        r = await _save(client, _headers(world["admin"], "admin"), values={str(world["first"].id): {"group": "gut"}})
        assert r.status_code == 422


class TestPrefillIsAnOfferNotAnApplication:
    @pytest.mark.asyncio
    async def test_the_preflight_reports_what_a_saved_design_would_contribute(self, client, world):
        headers = _headers(world["admin"], "admin")
        await _save(client, headers, experiment_id=world["exp"].id, values={str(world["first"].id): {"group": "gut"}})

        r = await client.post(
            "/api/pipeline-runs/preflight",
            json={
                "pipeline_key": KEY,
                "experiment_id": world["exp"].id,
                "sample_ids": [world["first"].id, world["second"].id],
            },
            headers=headers,
        )
        assert r.status_code == 200
        prefill = r.json()["prefill"]
        assert prefill["scope"] == "experiment"
        assert prefill["values"] == {str(world["first"].id): {"group": "gut"}}
        # The second sample was added after the design was set. It is named so the
        # grid can arrive blank for it rather than looking answered.
        assert prefill["samples_without_values"] == [world["second"].id]

    @pytest.mark.asyncio
    async def test_a_prefill_does_not_reach_the_sheet_on_its_own(self, client, world):
        """The saved design is offered, not applied: the launch still blocks.

        Silently filling the sheet from a stored mapping is the failure this
        project exists to remove. The scientist confirms the design in the grid,
        and it reaches the sheet because they sent it.
        """
        headers = _headers(world["admin"], "admin")
        await _save(
            client,
            headers,
            experiment_id=world["exp"].id,
            values={
                str(world["first"].id): {"group": "gut", "short_reads_platform": "illumina"},
                str(world["second"].id): {"group": "gut", "short_reads_platform": "illumina"},
            },
        )

        r = await client.post(
            "/api/pipeline-runs/preflight",
            json={
                "pipeline_key": KEY,
                "experiment_id": world["exp"].id,
                "sample_ids": [world["first"].id, world["second"].id],
            },
            headers=headers,
        )
        body = r.json()
        assert body["prefill"]["values"] != {}
        assert body["can_launch"] is False
        assert "group" in body["details"]["missing_columns"]

    @pytest.mark.asyncio
    async def test_no_saved_design_reports_no_scope_rather_than_nothing(self, client, world):
        r = await client.post(
            "/api/pipeline-runs/preflight",
            json={"pipeline_key": KEY, "experiment_id": world["exp"].id, "sample_ids": [world["first"].id]},
            headers=_headers(world["admin"], "admin"),
        )
        assert r.json()["prefill"]["scope"] is None
        assert r.json()["prefill"]["values"] == {}
