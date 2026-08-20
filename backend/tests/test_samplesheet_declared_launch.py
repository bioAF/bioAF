"""A declared sheet, saved and then actually launched.

``test_samplesheet_declaration.py`` pins what a declaration MEANS. This pins the
half that was missing and that no unit test could have caught, because the gap
was in the emitted sheet: a pipeline with no contract produced a byte-identical
``sample,fastq_1,fastq_2`` file whatever the scientist stated, and the entry grid
asked for nothing.

The two properties that matter end to end:

**A declaration reaches the file.** What is saved on the mapping is what the
preflight previews and what the launch submits, or the review step confirms a
sheet other than the one that runs.

**Declaring nothing changes nothing.** A pipeline with no contract and no
declaration still gets today's generic sheet. "No schema" means "we do not know",
never a refusal and never a different file.
"""

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample, sample_files
from app.models.sample_custom_field import SampleCustomField
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles

KEY = "nf-core/mcmicro"


def _headers(user, role_name: str) -> dict:
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)
    return {"Authorization": f"Bearer {token}"}


DECLARATION = [
    {
        "name": "sample",
        "type": "string",
        "required": True,
        "binding": {"source": "sample_field", "key": "external_id"},
    },
    {"name": "image", "type": "file", "required": True, "binding": {"source": "file_type", "key": "tiff"}},
    {
        "name": "marker_panel",
        "type": "string",
        "required": False,
        "binding": {"source": "custom_field", "key": "panel"},
    },
    {"name": "cycle", "type": "string", "required": True},
]


@pytest_asyncio.fixture
async def world(session):
    org = Organization(name="DeclaredOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)

    admin = User(
        email="admin@declared.test",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(admin)
    await session.flush()

    exp = Experiment(name="Imaging", organization_id=org.id, status="fastq_uploaded", owner_user_id=admin.id)
    session.add(exp)
    await session.flush()

    # A pipeline that publishes NO contract. This is the population step 2 never
    # covered: 17 of them in the catalog.
    session.add(
        PipelineCatalogEntry(
            organization_id=org.id,
            pipeline_key=KEY,
            name=KEY,
            source_type="nf-core",
            source_url=f"https://github.com/{KEY}",
            version="1.0.0",
            default_params_json={},
            input_schema_json={"absent": True},
            enabled=True,
        )
    )

    sample = Sample(experiment_id=exp.id, external_id="SLIDE-1", organism="Homo sapiens")
    session.add(sample)
    await session.flush()

    image = File(
        organization_id=org.id,
        experiment_id=exp.id,
        filename="SLIDE-1.ome.tiff",
        storage_uri="gs://bucket/SLIDE-1.ome.tiff",
        file_type="tiff",
        source_type="upload",
        size_bytes=10,
    )
    reads = File(
        organization_id=org.id,
        experiment_id=exp.id,
        filename="SLIDE-1_R1_001.fastq.gz",
        storage_uri="gs://bucket/SLIDE-1_R1_001.fastq.gz",
        file_type="fastq",
        source_type="upload",
        size_bytes=10,
    )
    session.add_all([image, reads, SampleCustomField(sample_id=sample.id, field_name="panel", field_value="PANEL_A")])
    await session.flush()
    for f in (image, reads):
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    await session.flush()
    await session.commit()
    return {"org": org, "admin": admin, "exp": exp, "sample": sample}


async def _save_declaration(client, headers, experiment_id, columns):
    return await client.post(
        "/api/samplesheet-mappings",
        json={
            "pipeline_key": KEY,
            "scope": "experiment",
            "experiment_id": experiment_id,
            "columns": columns,
        },
        headers=headers,
    )


async def _preflight(client, headers, world, **extra):
    body = {
        "pipeline_key": KEY,
        "experiment_id": world["exp"].id,
        "sample_ids": [world["sample"].id],
    }
    body.update(extra)
    return await client.post("/api/pipeline-runs/preflight", json=body, headers=headers)


class TestADeclarationReachesTheFile:
    @pytest.mark.asyncio
    async def test_the_declared_columns_are_the_sheet(self, client, world):
        headers = _headers(world["admin"], "admin")
        assert (await _save_declaration(client, headers, world["exp"].id, DECLARATION)).status_code == 200

        r = await _preflight(client, headers, world, sample_values={str(world["sample"].id): {"cycle": "1"}})

        assert r.status_code == 200
        sheet = r.json()["samplesheet"]
        assert sheet["columns"] == ["sample", "image", "marker_panel", "cycle"]
        assert sheet["csv"].splitlines()[1] == "SLIDE-1,gs://bucket/SLIDE-1.ome.tiff,PANEL_A,1"

    @pytest.mark.asyncio
    async def test_a_required_declared_column_blocks_the_launch(self, client, world):
        """`cycle` is bound to nothing, so it is asked per sample. Unanswered, it
        blocks, and the block names the column."""
        headers = _headers(world["admin"], "admin")
        await _save_declaration(client, headers, world["exp"].id, DECLARATION)

        r = await _preflight(client, headers, world)

        assert r.json()["can_launch"] is False
        assert "cycle" in r.json()["details"]["missing_columns"]

    @pytest.mark.asyncio
    async def test_the_grid_asks_for_the_unbound_column(self, client, world):
        headers = _headers(world["admin"], "admin")
        await _save_declaration(client, headers, world["exp"].id, DECLARATION)

        r = await _preflight(client, headers, world)

        assert [spec["name"] for spec in r.json()["per_sample_inputs"]] == ["cycle"]

    @pytest.mark.asyncio
    async def test_the_declaration_is_offered_back_as_prefill(self, client, world):
        headers = _headers(world["admin"], "admin")
        await _save_declaration(client, headers, world["exp"].id, DECLARATION)

        r = await _preflight(client, headers, world)

        assert [c["name"] for c in r.json()["prefill"]["columns"]] == [
            "sample",
            "image",
            "marker_panel",
            "cycle",
        ]

    @pytest.mark.asyncio
    async def test_a_saved_declaration_reads_back_unchanged(self, client, world):
        headers = _headers(world["admin"], "admin")
        await _save_declaration(client, headers, world["exp"].id, DECLARATION)

        r = await client.get(
            "/api/samplesheet-mappings",
            params={"pipeline_key": KEY, "experiment_id": world["exp"].id},
            headers=headers,
        )

        columns = r.json()["columns"]
        assert [c["name"] for c in columns] == ["sample", "image", "marker_panel", "cycle"]
        assert columns[1]["binding"] == {"source": "file_type", "key": "tiff"}
        assert columns[3].get("binding") in (None, {})

    @pytest.mark.asyncio
    async def test_a_declaration_bioaf_cannot_honour_is_refused_when_it_is_saved(self, client, world):
        """Refused at the door, not at launch. A binding bioAF cannot resolve
        would leave a column permanently unanswerable, and finding that out at
        launch time is exactly the late failure this project removes."""
        headers = _headers(world["admin"], "admin")

        r = await _save_declaration(
            client,
            headers,
            world["exp"].id,
            [{"name": "x", "type": "string", "binding": {"source": "sql_query", "key": "select 1"}}],
        )

        assert r.status_code == 422


class TestOnlyAPipelineWithNothingToGoOnIsDeclarable:
    @pytest.mark.asyncio
    async def test_a_pipeline_with_no_contract_is_declarable(self, client, world):
        r = await _preflight(client, _headers(world["admin"], "admin"), world)

        assert r.json()["declaration"]["declarable"] is True

    @pytest.mark.asyncio
    async def test_the_binding_vocabulary_is_what_these_samples_carry(self, client, world):
        """Chosen from what exists, not typed from memory: a file type that
        matches nothing binds to nothing and blocks with no hint as to why."""
        r = await _preflight(client, _headers(world["admin"], "admin"), world)

        assert r.json()["declaration"]["file_types"] == ["fastq", "tiff"]
        assert r.json()["declaration"]["custom_fields"] == ["panel"]


class TestDeclaringNothingChangesNothing:
    @pytest.mark.asyncio
    async def test_the_generic_sheet_is_unchanged(self, client, world):
        """The load-bearing rule from the plan: no schema means today's
        behaviour, never a refusal."""
        r = await _preflight(client, _headers(world["admin"], "admin"), world)

        assert r.json()["samplesheet"]["csv"].splitlines()[0] == "sample,fastq_1,fastq_2"
        assert r.json()["can_launch"] is True


class TestTheDeclarationOnScreenBindsThisRun:
    """The half that was missing, found by driving the wizard in a browser.

    Saving is deliberate and stays deliberate: design 02 section 4, and nothing
    here promotes anything. What changes is that saving stops being the ONLY way
    a declaration reaches a run.

    Before this, a scientist declared columns, the editor said they were emitted
    in that order, the review said "the samplesheet this run will submit", and
    the sheet shown and submitted was bioAF's standard three. The declaration
    bound a LATER run, and only if they also pressed a button presented as being
    about next time. That is the one property the review step exists to provide,
    stated in ``_effective_contract``'s own docstring: two resolutions would let
    the review confirm a sheet other than the one that runs.

    The three-way distinction below is the whole of it, and the middle case is
    the one a refactor will get wrong:

        absent    -> whatever is saved. The wizard has nothing to say yet
        []        -> nothing in force. The scientist cleared the editor
        [...]     -> this, for this run, saved or not
    """

    @pytest.mark.asyncio
    async def test_an_unsaved_declaration_is_the_previewed_sheet(self, client, world):
        """Nothing saved anywhere. The sheet is what is on screen."""
        headers = _headers(world["admin"], "admin")

        r = await _preflight(
            client,
            headers,
            world,
            columns=DECLARATION,
            sample_values={str(world["sample"].id): {"cycle": "1"}},
        )

        assert r.status_code == 200
        sheet = r.json()["samplesheet"]
        assert sheet["columns"] == ["sample", "image", "marker_panel", "cycle"]
        assert sheet["csv"].splitlines()[1] == "SLIDE-1,gs://bucket/SLIDE-1.ome.tiff,PANEL_A,1"

    @pytest.mark.asyncio
    async def test_an_unsaved_declaration_is_judged_like_a_saved_one(self, client, world):
        """The block and the grid come from the same contract as the preview, so
        an unbound required column stops the launch whether or not it was saved."""
        headers = _headers(world["admin"], "admin")

        r = await _preflight(client, headers, world, columns=DECLARATION)

        assert r.json()["can_launch"] is False
        assert "cycle" in r.json()["details"]["missing_columns"]
        assert [spec["name"] for spec in r.json()["per_sample_inputs"]] == ["cycle"]

    @pytest.mark.asyncio
    async def test_using_a_declaration_does_not_save_it(self, client, world):
        """Nothing is promoted by launching. A one-off accommodation must never
        become what the next person inherits."""
        headers = _headers(world["admin"], "admin")

        await _preflight(client, headers, world, columns=DECLARATION)

        r = await client.get(
            "/api/samplesheet-mappings",
            params={"pipeline_key": KEY, "experiment_id": world["exp"].id},
            headers=headers,
        )
        assert r.json()["columns"] == []

    @pytest.mark.asyncio
    async def test_the_screen_wins_over_what_was_saved(self, client, world):
        """Editing a saved declaration and launching without re-saving runs what
        is on screen. The alternative is a review that confirms the old sheet."""
        headers = _headers(world["admin"], "admin")
        await _save_declaration(client, headers, world["exp"].id, DECLARATION)

        r = await _preflight(
            client,
            headers,
            world,
            columns=[
                {
                    "name": "sample",
                    "type": "string",
                    "required": True,
                    "binding": {"source": "sample_field", "key": "external_id"},
                },
                {
                    "name": "slide",
                    "type": "file",
                    "required": True,
                    "binding": {"source": "file_type", "key": "tiff"},
                },
            ],
        )

        assert r.json()["samplesheet"]["columns"] == ["sample", "slide"]

    @pytest.mark.asyncio
    async def test_omitting_columns_still_reads_the_saved_declaration(self, client, world):
        """The regression this is most likely to cause. A request that says
        nothing about columns must not be read as declaring none, or every
        client that has not been updated silently loses its saved sheet."""
        headers = _headers(world["admin"], "admin")
        await _save_declaration(client, headers, world["exp"].id, DECLARATION)

        r = await _preflight(client, headers, world)

        assert r.json()["samplesheet"]["columns"] == ["sample", "image", "marker_panel", "cycle"]

    @pytest.mark.asyncio
    async def test_an_emptied_editor_is_not_the_saved_declaration(self, client, world):
        """Cleared on screen is a statement, and a different one from silence.
        It means today's generic sheet, which is what "no declaration" has always
        meant."""
        headers = _headers(world["admin"], "admin")
        await _save_declaration(client, headers, world["exp"].id, DECLARATION)

        r = await _preflight(client, headers, world, columns=[])

        assert r.json()["samplesheet"]["csv"].splitlines()[0] == "sample,fastq_1,fastq_2"

    @pytest.mark.asyncio
    async def test_a_declaration_bioaf_cannot_honour_falls_back_rather_than_failing(self, client, world):
        """Refused at SAVE time, so this is the odd path: a binding that got here
        anyway. An unlaunchable pipeline is worse than an un-customised one, so
        it falls back to the generic sheet rather than 500-ing the preflight."""
        headers = _headers(world["admin"], "admin")

        r = await _preflight(
            client,
            headers,
            world,
            columns=[{"name": "x", "type": "string", "binding": {"source": "sql_query", "key": "select 1"}}],
        )

        assert r.status_code == 200
        assert r.json()["samplesheet"]["csv"].splitlines()[0] == "sample,fastq_1,fastq_2"
