"""Answering "can this actually launch?" before the user presses Launch.

The pre-flight decisions are already correct, but they ran only inside
launch_run, so the user learned about them after clicking through every step of
the wizard. Driving the UI showed 5 of the 20 most popular pipelines failing that
way with nothing said beforehand.

This endpoint runs the same checks and returns the same structured answer
without creating a run, a linkage row, or a compute call.
"""

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _schema(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest_asyncio.fixture
async def base(session):
    org = Organization(name="PreflightOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    user = User(
        email="preflight@test.com",
        password_hash=AuthService.hash_password("testpass123"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    exp = Experiment(name="Preflight Exp", organization_id=org.id, status="fastq_uploaded", owner_user_id=user.id)
    session.add(exp)
    await session.flush()

    for key, name, version, schema in [
        ("nf-core/demo", "nf-core/demo", "1.2.0", _schema("demo")),
        ("nf-core/funcscan", "nf-core/funcscan", "4.0.0", _schema("funcscan")),
        ("nf-core/mag", "nf-core/mag", "5.5.0", _schema("mag")),
        ("nf-core/sarek", "nf-core/sarek", "3.9.0", _schema("sarek")),
    ]:
        session.add(
            PipelineCatalogEntry(
                organization_id=org.id,
                pipeline_key=key,
                name=name,
                source_type="nf-core",
                source_url=f"https://github.com/{name}",
                version=version,
                default_params_json={},
                input_schema_json=schema,
                enabled=True,
            )
        )
    await session.flush()

    sample = Sample(experiment_id=exp.id, external_id="SAMPLE-1", organism="Homo sapiens")
    session.add(sample)
    await session.flush()
    f = File(
        organization_id=org.id,
        experiment_id=exp.id,
        gcs_uri="gs://b/SAMPLE-1_R1_001.fastq.gz",
        filename="SAMPLE-1_R1_001.fastq.gz",
        file_type="fastq",
        source_type="upload",
    )
    session.add(f)
    await session.flush()
    await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    await session.commit()
    return {"org": org, "user": user, "exp": exp, "sample": sample}


async def _token(client, email="preflight@test.com", password="testpass123"):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return r.json()["access_token"]


async def _preflight(client, token, key, exp_id, sample_ids, parameters=None):
    return await client.post(
        "/api/pipeline-runs/preflight",
        json={
            "pipeline_key": key,
            "experiment_id": exp_id,
            "sample_ids": sample_ids,
            "parameters": parameters or {},
        },
        headers={"Authorization": f"Bearer {token}"},
    )


class TestPreflightReportsTheSameDecisionAsLaunch:
    @pytest.mark.asyncio
    async def test_a_launchable_pipeline_reports_ok(self, client, base):
        token = await _token(client)
        r = await _preflight(client, token, "nf-core/demo", base["exp"].id, [base["sample"].id])

        assert r.status_code == 200
        assert r.json()["can_launch"] is True
        assert r.json()["reason"] is None

    @pytest.mark.asyncio
    async def test_a_pipeline_missing_its_input_file_reports_the_column(self, client, base):
        """funcscan wants an assembly, and bioAF holds arbitrary files per sample,
        so it is no longer refused outright. This sample carries reads instead, so
        the preflight names the column to attach."""
        token = await _token(client)
        r = await _preflight(client, token, "nf-core/funcscan", base["exp"].id, [base["sample"].id])

        assert r.status_code == 200
        body = r.json()
        assert body["can_launch"] is False
        assert body["code"] == "samples_missing_required_fields"
        assert "fasta" in body["details"]["missing_columns"]

    @pytest.mark.asyncio
    async def test_a_missing_required_column_reports_the_column(self, client, base):
        token = await _token(client)
        r = await _preflight(client, token, "nf-core/mag", base["exp"].id, [base["sample"].id])

        body = r.json()
        assert body["can_launch"] is False
        assert body["code"] == "samples_missing_required_fields"
        assert "group" in body["details"]["missing_columns"]

    @pytest.mark.asyncio
    async def test_it_names_the_sample_field_and_offending_samples(self, client, base):
        token = await _token(client)
        r = await _preflight(client, token, "nf-core/sarek", base["exp"].id, [base["sample"].id])

        detail = r.json()["details"]["missing_columns"]["patient"]
        assert detail["sample_field"] == "donor_source"
        assert [s["external_id"] for s in detail["samples"]] == ["SAMPLE-1"]


class TestPreflightIsFree:
    @pytest.mark.asyncio
    async def test_it_creates_no_run(self, client, base, session):
        token = await _token(client)
        before = await session.scalar(select(func.count()).select_from(PipelineRun))

        await _preflight(client, token, "nf-core/demo", base["exp"].id, [base["sample"].id])
        await _preflight(client, token, "nf-core/funcscan", base["exp"].id, [base["sample"].id])

        assert await session.scalar(select(func.count()).select_from(PipelineRun)) == before

    @pytest.mark.asyncio
    async def test_an_unknown_pipeline_is_a_404(self, client, base):
        token = await _token(client)
        r = await _preflight(client, token, "nf-core/does-not-exist", base["exp"].id, [base["sample"].id])

        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_it_requires_authentication(self, client, base):
        r = await client.post(
            "/api/pipeline-runs/preflight",
            json={"pipeline_key": "nf-core/demo", "experiment_id": base["exp"].id, "sample_ids": []},
        )

        assert r.status_code in (401, 403)


class TestThePreflightAsksTheQuestionTheLaunchWillAnswer:
    """A preview that can differ from the submitted sheet is worse than none.

    Prior pipeline outputs are excluded from a run's inputs by default, because
    feeding them back compounded the dataset every run. ``include_derived_inputs``
    opts in, and the preflight ignored it: it resolved every sample's files with
    the flag hardcoded off. So a launch that opted in was previewed with empty
    read columns and then submitted populated ones, and the scientist approved a
    sheet that was not the one that ran.

    Found on the demo, where every FASTQ arrived via fetchngs and is therefore a
    pipeline output.
    """

    @pytest.mark.asyncio
    async def test_a_derived_input_is_absent_from_the_sheet_by_default(self, client, base, session):
        derived = File(
            organization_id=base["org"].id,
            experiment_id=base["exp"].id,
            gcs_uri="gs://b/DERIVED-1_R1_001.fastq.gz",
            filename="DERIVED-1_R1_001.fastq.gz",
            file_type="fastq",
            source_type="pipeline_output",
        )
        session.add(derived)
        await session.flush()
        sample = Sample(experiment_id=base["exp"].id, external_id="DERIVED-1", organism="Homo sapiens")
        session.add(sample)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=derived.id))
        await session.commit()

        token = await _token(client)
        r = await _preflight(client, token, "nf-core/demo", base["exp"].id, [sample.id])

        row = r.json()["samplesheet"]["rows"][0]["values"]
        assert "DERIVED-1_R1_001.fastq.gz" not in ",".join(row)

    @pytest.mark.asyncio
    async def test_opting_in_previews_the_sheet_that_would_be_submitted(self, client, base, session):
        derived = File(
            organization_id=base["org"].id,
            experiment_id=base["exp"].id,
            gcs_uri="gs://b/DERIVED-2_R1_001.fastq.gz",
            filename="DERIVED-2_R1_001.fastq.gz",
            file_type="fastq",
            source_type="pipeline_output",
        )
        session.add(derived)
        await session.flush()
        sample = Sample(experiment_id=base["exp"].id, external_id="DERIVED-2", organism="Homo sapiens")
        session.add(sample)
        await session.flush()
        await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=derived.id))
        await session.commit()

        token = await _token(client)
        r = await client.post(
            "/api/pipeline-runs/preflight",
            json={
                "pipeline_key": "nf-core/demo",
                "experiment_id": base["exp"].id,
                "sample_ids": [sample.id],
                "parameters": {},
                "include_derived_inputs": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        row = r.json()["samplesheet"]["rows"][0]["values"]
        assert "DERIVED-2_R1_001.fastq.gz" in ",".join(row)
