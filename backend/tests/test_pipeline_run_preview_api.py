"""The launch dialog asking what this run would submit, before it submits it.

Preflight already answered "would this launch succeed". The review step needs
the other half of the same question: what sheet would go to Nextflow, and what
is still outstanding. Both come from one call, because they are one computation,
and two answers that could disagree would be worse than either alone.

Values a scientist states travel on the launch request as a first-class field
rather than inside ``parameters``. ``parameters`` is emitted verbatim onto the
Nextflow command line, one ``--key value`` per entry, so a design grid smuggled
in there would become a bogus ``--sample_values`` argument.
"""

import json
from pathlib import Path

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _schema(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest_asyncio.fixture
async def base(session):
    org = Organization(name="PreviewOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    user = User(
        email="preview@test.com",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    exp = Experiment(name="Preview Exp", organization_id=org.id, status="fastq_uploaded", owner_user_id=user.id)
    session.add(exp)
    await session.flush()

    for key, version, schema in [
        ("nf-core/demo", "1.2.0", _schema("demo")),
        ("nf-core/mag", "5.5.0", _schema("mag")),
    ]:
        session.add(
            PipelineCatalogEntry(
                organization_id=org.id,
                pipeline_key=key,
                name=key,
                source_type="nf-core",
                source_url=f"https://github.com/{key}",
                version=version,
                default_params_json={},
                input_schema_json=schema,
                enabled=True,
            )
        )
    await session.flush()

    sample = Sample(experiment_id=exp.id, external_id="GUT_A", organism="Homo sapiens")
    session.add(sample)
    await session.flush()
    for mate in ("R1", "R2"):
        f = File(
            organization_id=org.id,
            experiment_id=exp.id,
            gcs_uri=f"gs://b/GUT_A_{mate}_001.fastq.gz",
            filename=f"GUT_A_{mate}_001.fastq.gz",
            file_type="fastq",
            source_type="upload",
        )
        session.add(f)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    await session.commit()
    return {"org": org, "user": user, "exp": exp, "sample": sample}


async def _token(client):
    r = await client.post("/api/auth/login", json={"email": "preview@test.com", "password": "testpass123"})
    return r.json()["access_token"]


async def _post(client, token, path, body):
    return await client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


def _answers(base):
    return {str(base["sample"].id): {"group": "gut", "short_reads_platform": "ILLUMINA"}}


class TestThePreflightShowsTheSheet:
    @pytest.mark.asyncio
    async def test_it_returns_the_sheet_this_run_would_submit(self, client, base):
        """The review step renders this. It is the generator's own output rather
        than a second rendering of it, so it cannot show a sheet the run did not
        use."""
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs/preflight",
            {"pipeline_key": "nf-core/demo", "experiment_id": base["exp"].id, "parameters": {}},
        )

        sheet = r.json()["samplesheet"]
        assert sheet["columns"][0] == "sample"
        assert sheet["csv"].startswith(",".join(sheet["columns"]))
        assert sheet["rows"][0]["external_id"] == "GUT_A"

    @pytest.mark.asyncio
    async def test_each_row_names_its_sample_so_a_cell_can_be_corrected(self, client, base):
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs/preflight",
            {"pipeline_key": "nf-core/demo", "experiment_id": base["exp"].id, "parameters": {}},
        )

        assert r.json()["samplesheet"]["rows"][0]["sample_id"] == base["sample"].id

    @pytest.mark.asyncio
    async def test_it_previews_a_sheet_that_is_still_blocked(self, client, base):
        """Seeing the empty column is how a user understands what the block is
        about, so the preview is not gated on the launch being possible."""
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs/preflight",
            {"pipeline_key": "nf-core/mag", "experiment_id": base["exp"].id, "parameters": {}},
        )

        body = r.json()
        assert body["can_launch"] is False
        assert body["samplesheet"]["rows"]


class TestThePreflightSaysWhatToAsk:
    @pytest.mark.asyncio
    async def test_it_lists_the_columns_the_grid_must_collect(self, client, base):
        """mag's `group` controls co-assembly and bioAF must never guess it. The
        dialog needs the question, not only the refusal."""
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs/preflight",
            {"pipeline_key": "nf-core/mag", "experiment_id": base["exp"].id, "parameters": {}},
        )

        assert "group" in {spec["name"] for spec in r.json()["per_sample_inputs"]}

    @pytest.mark.asyncio
    async def test_it_asks_for_nothing_when_the_pipeline_is_satisfied(self, client, base):
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs/preflight",
            {"pipeline_key": "nf-core/demo", "experiment_id": base["exp"].id, "parameters": {}},
        )

        assert r.json()["per_sample_inputs"] == []


class TestStatedValuesTravelWithTheLaunch:
    @pytest.mark.asyncio
    async def test_they_change_the_verdict_and_the_sheet(self, client, base):
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs/preflight",
            {
                "pipeline_key": "nf-core/mag",
                "experiment_id": base["exp"].id,
                "parameters": {},
                "sample_values": _answers(base),
            },
        )

        body = r.json()
        assert body["can_launch"] is True
        assert body["per_sample_inputs"] == []
        assert "gut" in body["samplesheet"]["csv"]

    @pytest.mark.asyncio
    async def test_the_launch_uses_what_the_preview_showed(self, client, base):
        """The point of confirming a preview: what was approved is what runs."""
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs",
            {
                "pipeline_key": "nf-core/mag",
                "experiment_id": base["exp"].id,
                "parameters": {},
                "sample_values": _answers(base),
            },
        )

        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_a_launch_without_them_is_still_refused(self, client, base):
        """The control: the launch check and the preview agree, so a run the
        preview called blocked stays blocked."""
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs",
            {"pipeline_key": "nf-core/mag", "experiment_id": base["exp"].id, "parameters": {}},
        )

        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_they_never_reach_the_nextflow_command_line(self, client, base):
        """`parameters` is emitted verbatim as --key value, which is why the
        design grid travels as its own field."""
        token = await _token(client)
        r = await _post(
            client,
            token,
            "/api/pipeline-runs",
            {
                "pipeline_key": "nf-core/mag",
                "experiment_id": base["exp"].id,
                "parameters": {},
                "sample_values": _answers(base),
            },
        )

        assert "sample_values" not in (r.json()["parameters"] or {})
