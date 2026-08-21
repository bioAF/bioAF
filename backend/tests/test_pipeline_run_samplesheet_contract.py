"""Launch-path enforcement of the pipeline's samplesheet contract.

The failure being removed is expensive and confusing: a pipeline bioAF cannot
build a valid sheet for still launches, scales up a node, pulls containers, and
dies inside Nextflow on a schema error the user did not write.

The guarantee these assert is that a refused launch costs NOTHING: no run row,
no sample linkage, no compute call. That is why the check sits before the run is
created rather than beside the sheet generation it protects.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.adapters.compute.kubernetes import _job_submit_result_from_dict

from app.exceptions import (
    SamplesMissingRequiredFieldsError,
)
from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.schemas.pipeline_run import PipelineRunLaunchRequest
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.pipeline_run_service import PipelineRunService

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _schema(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest_asyncio.fixture
async def base(session):
    org = Organization(name="ContractOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="contract@test.com",
        password_hash=AuthService.hash_password("testpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    exp = Experiment(name="Contract Experiment", organization_id=org.id, status="fastq_uploaded", owner_user_id=user.id)
    session.add(exp)
    await session.flush()

    entries = [
        # requires `patient`, which bioAF sources from Sample.donor_source
        PipelineCatalogEntry(
            organization_id=org.id,
            pipeline_key="nf-core/sarek",
            name="nf-core/sarek",
            source_type="nf-core",
            source_url="https://github.com/nf-core/sarek",
            version="3.9.0",
            default_params_json={},
            input_schema_json=_schema("sarek"),
            enabled=True,
        ),
        # takes assemblies, not reads: cannot be launched from samples at all
        PipelineCatalogEntry(
            organization_id=org.id,
            pipeline_key="nf-core/funcscan",
            name="nf-core/funcscan",
            source_type="nf-core",
            source_url="https://github.com/nf-core/funcscan",
            version="4.0.0",
            default_params_json={},
            input_schema_json=_schema("funcscan"),
            enabled=True,
        ),
        # already valid today: the no-regression control
        PipelineCatalogEntry(
            organization_id=org.id,
            pipeline_key="nf-core/demo",
            name="nf-core/demo",
            source_type="nf-core",
            source_url="https://github.com/nf-core/demo",
            version="1.2.0",
            default_params_json={},
            input_schema_json=_schema("demo"),
            enabled=True,
        ),
    ]
    session.add_all(entries)
    await session.flush()
    await session.commit()
    return {"org": org, "user": user, "exp": exp}


async def _sample_with_reads(session, org, exp, external_id, **fields):
    s = Sample(experiment_id=exp.id, external_id=external_id, **fields)
    session.add(s)
    await session.flush()
    f = File(
        organization_id=org.id,
        experiment_id=exp.id,
        gcs_uri=f"gs://bucket/{external_id}_R1_001.fastq.gz",
        filename=f"{external_id}_R1_001.fastq.gz",
        file_type="fastq",
        source_type="upload",
    )
    session.add(f)
    await session.flush()
    await session.execute(sample_files.insert().values(sample_id=s.id, file_id=f.id))
    await session.flush()
    return s


def _capturing_adapter(captured: dict):
    """A compute adapter that records the job spec instead of submitting it."""

    async def capture_submit(job_spec):
        captured.update(job_spec)
        return _job_submit_result_from_dict(
            {
                "job_id": "bioaf-contract-test",
                "namespace": "bioaf-pipelines",
                "status": "queued",
                "estimated_cost": {"estimated_cost_usd": 0.50},
            }
        )

    adapter = MagicMock()
    adapter.submit_job = AsyncMock(side_effect=capture_submit)
    return adapter


async def _counts(session, experiment_id: int):
    runs = await session.scalar(
        select(func.count()).select_from(PipelineRun).where(PipelineRun.experiment_id == experiment_id)
    )
    links = await session.scalar(select(func.count()).select_from(PipelineRunSample))
    return runs, links


class TestContractBlocksBeforeAnythingIsProvisioned:
    @pytest.mark.asyncio
    async def test_pipeline_missing_its_input_file_is_blocked(self, session, base):
        """nf-core/funcscan wants an assembly. This sample carries only reads, so
        it would receive a sheet with no fasta and fail inside Nextflow."""
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _sample_with_reads(session, org, exp, "SAMPLE-201", donor_source="D1")
        await session.commit()

        req = PipelineRunLaunchRequest(pipeline_key="nf-core/funcscan", experiment_id=exp.id, sample_ids=[s.id])
        with pytest.raises(SamplesMissingRequiredFieldsError) as ei:
            await PipelineRunService.launch_run(session, org.id, user.id, req)

        assert "fasta" in ei.value.details["missing_columns"]

    @pytest.mark.asyncio
    async def test_refusal_creates_no_run_and_no_linkage(self, session, base):
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _sample_with_reads(session, org, exp, "SAMPLE-202", donor_source="D1")
        await session.commit()
        # Held as plain ints: the ORM objects expire on rollback below, and
        # touching them afterwards would trigger lazy IO rather than assert.
        exp_id, org_id, user_id, sample_id = exp.id, org.id, user.id, s.id
        before = await _counts(session, exp_id)

        req = PipelineRunLaunchRequest(pipeline_key="nf-core/funcscan", experiment_id=exp_id, sample_ids=[sample_id])
        with pytest.raises(SamplesMissingRequiredFieldsError):
            await PipelineRunService.launch_run(session, org_id, user_id, req)
        await session.rollback()

        assert await _counts(session, exp_id) == before

    @pytest.mark.asyncio
    async def test_refusal_never_calls_the_compute_adapter(self, session, base):
        """The whole point: no node is scaled up to discover a schema error."""
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _sample_with_reads(session, org, exp, "SAMPLE-203", donor_source="D1")
        await session.commit()

        req = PipelineRunLaunchRequest(pipeline_key="nf-core/funcscan", experiment_id=exp.id, sample_ids=[s.id])
        with patch("app.services.pipeline_run_service.get_compute_adapter") as adapter:
            adapter.return_value.submit_job = AsyncMock()
            with pytest.raises(SamplesMissingRequiredFieldsError):
                await PipelineRunService.launch_run(session, org.id, user.id, req)
            adapter.return_value.submit_job.assert_not_called()


class TestRequiredFieldsMustBeSourceable:
    @pytest.mark.asyncio
    async def test_sarek_blocks_when_donor_source_is_empty(self, session, base):
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _sample_with_reads(session, org, exp, "SAMPLE-301")  # no donor_source
        await session.commit()

        req = PipelineRunLaunchRequest(pipeline_key="nf-core/sarek", experiment_id=exp.id, sample_ids=[s.id])
        with pytest.raises(SamplesMissingRequiredFieldsError) as ei:
            await PipelineRunService.launch_run(session, org.id, user.id, req)

        detail = ei.value.details["missing_columns"]["patient"]
        assert detail["sample_field"] == "donor_source"
        assert [x["external_id"] for x in detail["samples"]] == ["SAMPLE-301"]

    @pytest.mark.asyncio
    async def test_sarek_launches_when_donor_source_is_present(self, session, base):
        """The headline unblock: sarek is launchable, and its sheet carries the
        patient column that today's generic sheet omits entirely."""
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _sample_with_reads(session, org, exp, "SAMPLE-302", donor_source="DONOR_7")
        await session.commit()

        captured: dict = {}
        req = PipelineRunLaunchRequest(pipeline_key="nf-core/sarek", experiment_id=exp.id, sample_ids=[s.id])
        with (
            patch(
                "app.services.pipeline_run_service.get_compute_adapter",
                return_value=_capturing_adapter(captured),
            ),
            patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
        ):
            run = await PipelineRunService.launch_run(session, org.id, user.id, req)

        assert run is not None
        sheet = captured["sample_sheet"]
        header = sheet.splitlines()[0].split(",")
        assert "patient" in header
        assert sheet.splitlines()[1].split(",")[header.index("patient")] == "DONOR_7"

    @pytest.mark.asyncio
    async def test_a_pipeline_needing_nothing_extra_is_unaffected(self, session, base):
        """nf-core/demo needs only sample and fastq_1, both of which bioAF has.
        It must keep launching exactly as it does today."""
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _sample_with_reads(session, org, exp, "SAMPLE-303")
        await session.commit()

        captured: dict = {}
        req = PipelineRunLaunchRequest(pipeline_key="nf-core/demo", experiment_id=exp.id, sample_ids=[s.id])
        with (
            patch(
                "app.services.pipeline_run_service.get_compute_adapter",
                return_value=_capturing_adapter(captured),
            ),
            patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
        ):
            run = await PipelineRunService.launch_run(session, org.id, user.id, req)

        assert run is not None
        assert captured["sample_sheet"].splitlines()[0] == "sample,fastq_1,fastq_2"
