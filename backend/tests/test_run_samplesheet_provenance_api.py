"""Reading a run's own samplesheet record back out of the API.

The run has kept the sheet and the design since the snapshot shipped, and
nothing served them, so the record existed and could not be read. Design
section 10 exists for defending a result later, which means a person has to be
able to see it.

The stamps are stored as user ids, which is the right key and the wrong thing to
show. "Who set this value" is answered with a name.
"""

import json
from pathlib import Path

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


@pytest_asyncio.fixture
async def base(session):
    org = Organization(name="RunProvenanceOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)

    wet_lab = User(
        email="wetlab@runprov.test",
        name="Wet Lab",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    launcher = User(
        email="bfx@runprov.test",
        name="Bioinformatician",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add_all([wet_lab, launcher])
    await session.flush()

    exp = Experiment(name="Prov Exp", organization_id=org.id, status="fastq_uploaded", owner_user_id=launcher.id)
    session.add(exp)
    await session.flush()

    session.add(
        PipelineCatalogEntry(
            organization_id=org.id,
            pipeline_key="nf-core/mag",
            name="nf-core/mag",
            source_type="nf-core",
            source_url="https://github.com/nf-core/mag",
            version="5.5.0",
            default_params_json={},
            input_schema_json=json.loads((FIXTURES / "mag.json").read_text()),
            enabled=True,
        )
    )
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
    return {"org": org, "wet_lab": wet_lab, "launcher": launcher, "exp": exp, "sample": sample}


def _headers(user) -> dict:
    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name="admin")
    return {"Authorization": f"Bearer {token}"}


async def _run_with_snapshot(session, base, *, csv: str | None, design: dict | None) -> PipelineRun:
    run = PipelineRun(
        organization_id=base["org"].id,
        experiment_id=base["exp"].id,
        submitted_by_user_id=base["launcher"].id,
        pipeline_name="nf-core/mag",
        pipeline_version="5.5.0",
        status="completed",
        samplesheet_csv=csv,
        samplesheet_mapping_json=design,
    )
    session.add(run)
    await session.flush()
    session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=base["sample"].id))
    await session.commit()
    return run


@pytest.mark.asyncio
async def test_the_run_serves_the_sheet_it_was_given(client, session, base):
    run = await _run_with_snapshot(
        session,
        base,
        csv="sample,short_reads_1,group\nGUT_A,gs://b/GUT_A_R1_001.fastq.gz,gut\n",
        design=None,
    )

    r = await client.get(f"/api/pipeline-runs/{run.id}", headers=_headers(base["launcher"]))

    assert r.status_code == 200
    assert r.json()["samplesheet_csv"].startswith("sample,short_reads_1,group")


@pytest.mark.asyncio
async def test_who_stated_a_value_is_answered_with_a_name(client, session, base):
    """The wet-lab scientist set the grouping; the bioinformatician launched.
    A launcher-only record names the wrong person for the value that was wrong."""
    design = {
        "values": {
            str(base["sample"].id): {
                "group": {
                    "value": "gut",
                    "set_by_user_id": base["wet_lab"].id,
                    "set_at": "2026-08-16T10:00:00+00:00",
                }
            }
        },
        "bindings": {},
    }
    run = await _run_with_snapshot(session, base, csv="sample,group\nGUT_A,gut\n", design=design)

    r = await client.get(f"/api/pipeline-runs/{run.id}", headers=_headers(base["launcher"]))

    stated = r.json()["samplesheet_design"]["values"][str(base["sample"].id)]["group"]
    assert stated["value"] == "gut"
    assert stated["set_by"] == "Wet Lab"
    assert stated["set_at"] == "2026-08-16T10:00:00+00:00"


@pytest.mark.asyncio
async def test_a_run_with_no_snapshot_reports_nothing_rather_than_a_reconstruction(client, session, base):
    """Runs launched before the snapshot existed have no sheet. Rebuilding one
    from today's samples and files would show a sheet the run never saw."""
    run = await _run_with_snapshot(session, base, csv=None, design=None)

    r = await client.get(f"/api/pipeline-runs/{run.id}", headers=_headers(base["launcher"]))

    assert r.json()["samplesheet_csv"] is None
    assert r.json()["samplesheet_design"] is None


@pytest.mark.asyncio
async def test_a_stamp_whose_user_is_gone_still_reports_the_value(client, session, base):
    """A value outlives the account that set it. Losing the whole record because
    a person left would be worse than an unnamed author."""
    design = {
        "values": {str(base["sample"].id): {"group": {"value": "gut", "set_by_user_id": 999_999, "set_at": None}}},
        "bindings": {},
    }
    run = await _run_with_snapshot(session, base, csv="sample,group\nGUT_A,gut\n", design=design)

    r = await client.get(f"/api/pipeline-runs/{run.id}", headers=_headers(base["launcher"]))

    stated = r.json()["samplesheet_design"]["values"][str(base["sample"].id)]["group"]
    assert stated["value"] == "gut"
    assert stated["set_by"] is None
