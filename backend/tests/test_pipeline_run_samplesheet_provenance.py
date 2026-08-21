"""What a run keeps about the sheet it was given.

Reproducing or defending a result later means knowing which file went into which
column. bioAF built that sheet, handed it to Nextflow and kept nothing, so the
only record of a run's actual inputs was whatever the samples happened to look
like afterwards, which is not the same thing: files get re-linked, a mapping gets
edited, a sample's metadata is corrected.

The run therefore keeps the exact sheet AND the design that produced it, as a
SNAPSHOT rather than a reference. A mapping edited next week must not rewrite the
history of a run that already used it. That is the whole point: a reference would
make the record change under the person relying on it.

The snapshot carries its authorship stamps with it, so "who set this value" is
answerable for a run that has already finished. Whoever fills the design grid is
often not whoever launches, so the launcher stamp alone names the wrong person
for the value that turned out wrong. That is what lets bioAF be used in a lab
operating under GLP, CLIA or any audited quality system.
"""

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.samplesheet_mapping_service import SamplesheetMappingService as Mappings

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


@pytest_asyncio.fixture
async def base(session):
    org = Organization(name="ProvenanceOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    user = User(
        email="provenance@test.com",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    exp = Experiment(name="Prov Exp", organization_id=org.id, status="fastq_uploaded", owner_user_id=user.id)
    session.add(exp)
    await session.flush()
    for key, version in [("nf-core/mag", "5.5.0"), ("nf-core/demo", "1.2.0")]:
        session.add(
            PipelineCatalogEntry(
                organization_id=org.id,
                pipeline_key=key,
                name=key,
                source_type="nf-core",
                source_url=f"https://github.com/{key}",
                version=version,
                default_params_json={},
                input_schema_json=json.loads((FIXTURES / f"{key.split('/')[1]}.json").read_text()),
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
    r = await client.post("/api/auth/login", json={"email": "provenance@test.com", "password": "testpass123"})
    return r.json()["access_token"]


async def _launch(client, base, values=None):
    token = await _token(client)
    return await client.post(
        "/api/pipeline-runs",
        json={
            "pipeline_key": "nf-core/mag",
            "experiment_id": base["exp"].id,
            "parameters": {},
            "sample_values": values
            if values is not None
            else {str(base["sample"].id): {"group": "gut", "short_reads_platform": "ILLUMINA"}},
        },
        headers={"Authorization": f"Bearer {token}"},
    )


async def _run(session, run_id):
    return await session.scalar(select(PipelineRun).where(PipelineRun.id == run_id))


class TestTheRunKeepsTheSheet:
    @pytest.mark.asyncio
    async def test_it_stores_the_exact_sheet_that_was_submitted(self, client, session, base):
        """Not a regeneration of it. Re-deriving the sheet later reads today's
        samples, today's files and today's mapping, none of which are what the
        run received."""
        r = await _launch(client, base)

        run = await _run(session, r.json()["id"])
        assert run.samplesheet_csv.splitlines()[0].startswith("sample,")
        assert "gut" in run.samplesheet_csv

    @pytest.mark.asyncio
    async def test_the_stored_sheet_is_the_one_the_preview_showed(self, client, session, base):
        """The scientist approved a preview. If the stored record differs from
        it, the record cannot be used to defend the result."""
        token = await _token(client)
        preview = await client.post(
            "/api/pipeline-runs/preflight",
            json={
                "pipeline_key": "nf-core/mag",
                "experiment_id": base["exp"].id,
                "parameters": {},
                "sample_values": {str(base["sample"].id): {"group": "gut", "short_reads_platform": "ILLUMINA"}},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        r = await _launch(client, base)
        run = await _run(session, r.json()["id"])

        assert run.samplesheet_csv == preview.json()["samplesheet"]["csv"]


class TestTheRunKeepsTheDesign:
    @pytest.mark.asyncio
    async def test_it_stores_the_values_that_produced_the_sheet(self, client, session, base):
        r = await _launch(client, base)

        run = await _run(session, r.json()["id"])
        stored = run.samplesheet_mapping_json["values"][str(base["sample"].id)]
        assert stored["group"]["value"] == "gut"

    @pytest.mark.asyncio
    async def test_each_value_names_who_set_it(self, client, session, base):
        """A launcher-only record names the wrong person when the wet-lab
        scientist set the design and the bioinformatician ran it."""
        r = await _launch(client, base)

        run = await _run(session, r.json()["id"])
        stored = run.samplesheet_mapping_json["values"][str(base["sample"].id)]
        assert stored["group"]["set_by_user_id"] == base["user"].id
        assert stored["group"]["set_at"]

    @pytest.mark.asyncio
    async def test_editing_the_mapping_afterwards_does_not_rewrite_the_run(self, client, session, base):
        """The reason it is a snapshot. A record that changes under the person
        relying on it is worse than no record."""
        r = await _launch(client, base)
        run_id = r.json()["id"]

        await Mappings.save(
            session,
            base["org"].id,
            base["user"].id,
            "nf-core/mag",
            "experiment",
            experiment_id=base["exp"].id,
            values={str(base["sample"].id): {"group": "CHANGED_LATER"}},
        )
        await session.commit()

        run = await _run(session, run_id)
        assert "CHANGED_LATER" not in json.dumps(run.samplesheet_mapping_json)
        assert "CHANGED_LATER" not in run.samplesheet_csv

    @pytest.mark.asyncio
    async def test_a_run_with_no_stated_values_records_an_empty_design(self, client, session, base):
        """Most runs state nothing, and "nothing was stated" is a fact worth
        recording rather than a null that could mean the feature was off."""
        token = await _token(client)
        r = await client.post(
            "/api/pipeline-runs",
            json={"pipeline_key": "nf-core/demo", "experiment_id": base["exp"].id, "parameters": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        run = await _run(session, r.json()["id"])
        assert run.samplesheet_mapping_json == {"values": {}, "bindings": {}}
