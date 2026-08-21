"""Saving a samplesheet design once and offering it back on the next run.

Adding samples after a run and re-running is normal in any biotech lab, and
re-typing a two-hundred-row co-assembly grouping each time is the cost this
removes. The hazard it must not introduce is a design carried silently into a run
it does not fit.

Three rules do that work, and each is pinned here.

**A mapping belongs to an EXPERIMENT.** Treating it as the pipeline's missing
contract was the earlier assumption and it is wrong: the right column for one
experiment is the wrong one for the next. It is promotable to the project and
then to the organization, deliberately at each rung, because a core facility runs
the same assay across unrelated projects and would otherwise reconfigure forever.

**Most specific scope wins, and the caller learns which one it used.** Naming the
source is the load-bearing half: an inherited organization-wide binding otherwise
looks identical to one somebody set for this experiment.

**Each value keeps the author who set it.** Whoever fills the design grid is
often not whoever launches, so a launcher-only record names the wrong person for
the value that turned out wrong. Re-saving to change one cell must not reassign
authorship of the rest.
"""

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.project import Project
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.samplesheet_mapping_service import SamplesheetMappingService as Mappings

KEY = "nf-core/mag"


@pytest_asyncio.fixture
async def world(session):
    org = Organization(name="MappingOrg", setup_complete=True)
    other_org = Organization(name="OtherOrg", setup_complete=True)
    session.add_all([org, other_org])
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)

    def _user(email):
        return User(
            email=email,
            password_hash=AuthService.hash_password("testpass123"),
            role_id=roles["admin"],
            organization_id=org.id,
            status="active",
        )

    wet_lab, bioinformatician = _user("wetlab@test.com"), _user("bfx@test.com")
    session.add_all([wet_lab, bioinformatician])
    await session.flush()

    project = Project(name="Gut Study", organization_id=org.id, owner_user_id=wet_lab.id)
    session.add(project)
    await session.flush()

    exp = Experiment(
        name="Run 1", organization_id=org.id, project_id=project.id, status="fastq_uploaded", owner_user_id=wet_lab.id
    )
    unparented = Experiment(
        name="No Project", organization_id=org.id, status="fastq_uploaded", owner_user_id=wet_lab.id
    )
    session.add_all([exp, unparented])
    await session.flush()
    await session.commit()
    return {
        "org": org,
        "other_org": other_org,
        "wet_lab": wet_lab,
        "bfx": bioinformatician,
        "project": project,
        "exp": exp,
        "unparented": unparented,
    }


async def _save(session, world, scope, values, user=None, **kw):
    return await Mappings.save(
        session,
        world["org"].id,
        (user or world["wet_lab"]).id,
        KEY,
        scope,
        experiment_id=kw.get("experiment_id", world["exp"].id if scope == "experiment" else None),
        project_id=kw.get("project_id", world["project"].id if scope == "project" else None),
        values=values,
    )


# -- The ladder --


class TestTheMostSpecificScopeWins:
    @pytest.mark.asyncio
    async def test_an_experiment_mapping_beats_the_project_and_the_organization(self, session, world):
        await _save(session, world, "organization", {"1": {"group": "org_wide"}})
        await _save(session, world, "project", {"1": {"group": "project_wide"}})
        await _save(session, world, "experiment", {"1": {"group": "this_experiment"}})

        mapping, scope = await Mappings.resolve(session, world["org"].id, KEY, world["exp"].id)

        assert scope == "experiment"
        assert Mappings.flatten(mapping) == {"1": {"group": "this_experiment"}}

    @pytest.mark.asyncio
    async def test_the_project_is_used_when_the_experiment_has_no_mapping(self, session, world):
        await _save(session, world, "organization", {"1": {"group": "org_wide"}})
        await _save(session, world, "project", {"1": {"group": "project_wide"}})

        mapping, scope = await Mappings.resolve(session, world["org"].id, KEY, world["exp"].id)

        assert scope == "project"
        assert Mappings.flatten(mapping) == {"1": {"group": "project_wide"}}

    @pytest.mark.asyncio
    async def test_the_organization_is_the_last_rung(self, session, world):
        """The rung that lets a core facility configure an assay once for every
        project it serves."""
        await _save(session, world, "organization", {"1": {"group": "org_wide"}})

        mapping, scope = await Mappings.resolve(session, world["org"].id, KEY, world["exp"].id)

        assert scope == "organization"

    @pytest.mark.asyncio
    async def test_an_experiment_outside_any_project_still_inherits_the_organization(self, session, world):
        """Not every experiment has a project. Skipping the missing rung is not
        the same as failing to resolve."""
        await _save(session, world, "organization", {"1": {"group": "org_wide"}})

        _, scope = await Mappings.resolve(session, world["org"].id, KEY, world["unparented"].id)

        assert scope == "organization"

    @pytest.mark.asyncio
    async def test_nothing_resolves_when_nothing_was_saved(self, session, world):
        mapping, scope = await Mappings.resolve(session, world["org"].id, KEY, world["exp"].id)

        assert (mapping, scope) == (None, None)

    @pytest.mark.asyncio
    async def test_another_pipelines_mapping_never_resolves(self, session, world):
        await _save(session, world, "experiment", {"1": {"group": "gut"}})

        mapping, _ = await Mappings.resolve(session, world["org"].id, "nf-core/sarek", world["exp"].id)

        assert mapping is None

    @pytest.mark.asyncio
    async def test_another_organizations_mapping_never_resolves(self, session, world):
        await _save(session, world, "organization", {"1": {"group": "org_wide"}})

        mapping, _ = await Mappings.resolve(session, world["other_org"].id, KEY, None)

        assert mapping is None


# -- One mapping per pipeline per scope --


class TestOneMappingPerScope:
    @pytest.mark.asyncio
    async def test_saving_twice_at_one_scope_edits_rather_than_adds(self, session, world):
        """Comparative work runs twice and each run keeps its own snapshot, so a
        scope never needs two rival mappings."""
        first = await _save(session, world, "experiment", {"1": {"group": "gut"}})
        second = await _save(session, world, "experiment", {"1": {"group": "skin"}})

        assert first.id == second.id
        assert Mappings.flatten(second) == {"1": {"group": "skin"}}

    @pytest.mark.asyncio
    async def test_the_database_refuses_a_second_organization_mapping(self, session, world):
        """The service looks before it writes, but the guarantee has to be the
        database's. This is the rung where a duplicate would matter most, and
        the one a plain unique constraint would miss: PostgreSQL treats NULLs as
        distinct, and an organization-scoped row has null experiment and project
        ids, so only a partial index actually holds it."""
        from sqlalchemy.exc import IntegrityError

        from app.models.samplesheet_mapping import SamplesheetMapping

        await _save(session, world, "organization", {"1": {"group": "org_wide"}})
        session.add(
            SamplesheetMapping(
                organization_id=world["org"].id,
                pipeline_key=KEY,
                scope="organization",
                values_json={},
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    @pytest.mark.asyncio
    async def test_promoting_leaves_the_experiment_mapping_alone(self, session, world):
        """Promotion is a deliberate act that adds a rung. It does not move the
        experiment's own configuration out from under it."""
        await _save(session, world, "experiment", {"1": {"group": "gut"}})
        await _save(session, world, "project", {"1": {"group": "gut"}})

        _, scope = await Mappings.resolve(session, world["org"].id, KEY, world["exp"].id)

        assert scope == "experiment"


# -- Authorship --


class TestEveryValueKeepsItsAuthor:
    @pytest.mark.asyncio
    async def test_a_value_records_who_set_it_and_when(self, session, world):
        mapping = await _save(session, world, "experiment", {"1": {"group": "gut"}})

        entry = mapping.values_json["1"]["group"]
        assert entry["value"] == "gut"
        assert entry["set_by_user_id"] == world["wet_lab"].id
        assert entry["set_at"]

    @pytest.mark.asyncio
    async def test_an_unchanged_value_keeps_its_original_author(self, session, world):
        """The load-bearing case. The wet-lab scientist set the design; the
        bioinformatician later corrects one unrelated cell. Recording the
        bioinformatician against all of it would answer "who last pressed save"
        rather than "who set this value"."""
        await _save(session, world, "experiment", {"1": {"group": "gut"}, "2": {"group": "skin"}})

        mapping = await _save(
            session,
            world,
            "experiment",
            {"1": {"group": "gut"}, "2": {"group": "oral"}},
            user=world["bfx"],
        )

        assert mapping.values_json["1"]["group"]["set_by_user_id"] == world["wet_lab"].id
        assert mapping.values_json["2"]["group"]["set_by_user_id"] == world["bfx"].id

    @pytest.mark.asyncio
    async def test_a_blank_answer_is_not_stored_as_an_answer(self, session, world):
        """A blank cell is an unanswered question, so a required column with no
        answer must go on blocking the launch rather than being satisfied by an
        empty string."""
        mapping = await _save(session, world, "experiment", {"1": {"group": "   "}})

        assert Mappings.flatten(mapping) == {}


# -- The shape generation consumes --


class TestItFeedsTheGeneratorDirectly:
    @pytest.mark.asyncio
    async def test_flattening_drops_the_authorship_the_sheet_does_not_need(self, session, world):
        mapping = await _save(session, world, "experiment", {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}})

        assert Mappings.flatten(mapping) == {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}

    @pytest.mark.asyncio
    async def test_flattening_nothing_is_an_empty_mapping_not_a_failure(self, session, world):
        assert Mappings.flatten(None) == {}
