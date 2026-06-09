"""Launch validation for samples that have no linked input files.

Regression coverage for the cross-sample file-contamination bug: when the
sample_files junction was empty, launch_run used to back-fill EVERY sample with
the ENTIRE experiment's FASTQ files, so one sample's process received another
sample's reads. The fallback is removed; a FASTQ-consuming pipeline now refuses
to launch a sample with no files, and the caller may opt to drop those samples.
"""

import pytest
import pytest_asyncio

from app.exceptions import SamplesMissingFilesError, ValidationError
from app.models.experiment import Experiment
from app.models.file import File
from app.models.organization import Organization
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRunSample
from app.models.sample import Sample, sample_files
from app.models.user import User
from app.schemas.pipeline_run import PipelineRunLaunchRequest
from app.services.auth_service import AuthService
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.pipeline_run_service import PipelineRunService


@pytest_asyncio.fixture
async def base(session):
    org = Organization(name="MissingFilesOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="missingfiles@test.com",
        password_hash=AuthService.hash_password("testpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    exp = Experiment(name="MF Experiment", organization_id=org.id, status="fastq_uploaded", owner_user_id=user.id)
    session.add(exp)
    await session.flush()
    # nf-core pipeline => consumes per-sample FASTQ input
    nfcore = PipelineCatalogEntry(
        organization_id=org.id,
        pipeline_key="nf-core/demo",
        name="nf-core/demo",
        source_type="nf-core",
        source_url="https://github.com/nf-core/demo",
        version="1.0.0",
        default_params_json={},
        is_builtin=True,
        enabled=True,
    )
    # builtin no-input pipeline => exempt from the files requirement
    builtin = PipelineCatalogEntry(
        organization_id=org.id,
        pipeline_key="bioaf-system-test",
        name="bioAF System Test",
        source_type="builtin",
        version="1.0.0",
        default_params_json={"message": "hi", "sleep_seconds": 1},
        is_builtin=True,
        enabled=True,
    )
    session.add_all([nfcore, builtin])
    await session.flush()
    await session.commit()
    return {"org": org, "user": user, "exp": exp}


async def _make_sample(session, exp, external_id):
    s = Sample(experiment_id=exp.id, external_id=external_id, organism="Homo sapiens")
    session.add(s)
    await session.flush()
    return s


async def _link_file(session, org, exp, sample, filename, source_type="upload"):
    f = File(
        organization_id=org.id,
        experiment_id=exp.id,
        gcs_uri=f"gs://bucket/{filename}",
        filename=filename,
        file_type="fastq",
        source_type=source_type,
    )
    session.add(f)
    await session.flush()
    await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
    await session.flush()
    return f


class TestLaunchRequiresSampleFiles:
    @pytest.mark.asyncio
    async def test_errors_when_a_sample_has_no_files(self, session, base):
        org, user, exp = base["org"], base["user"], base["exp"]
        s_with = await _make_sample(session, exp, "SAMPLE-101")
        await _link_file(session, org, exp, s_with, "SAMPLE-101_R1_001.fastq.gz")
        s_without = await _make_sample(session, exp, "SAMPLE-102")
        await session.commit()

        req = PipelineRunLaunchRequest(
            pipeline_key="nf-core/demo",
            experiment_id=exp.id,
            sample_ids=[s_with.id, s_without.id],
        )
        with pytest.raises(SamplesMissingFilesError) as ei:
            await PipelineRunService.launch_run(session, org.id, user.id, req)

        offending = ei.value.details["samples_without_files"]
        ids = {o["id"] for o in offending}
        assert ids == {s_without.id}, "only the file-less sample should be reported"
        assert any(o["external_id"] == "SAMPLE-102" for o in offending)

    @pytest.mark.asyncio
    async def test_no_cross_contamination_when_dropping_missing(self, session, base):
        """The bug: the file-less sample used to be back-filled with the other
        sample's files. With drop, it is removed and the survivor keeps only its own."""
        org, user, exp = base["org"], base["user"], base["exp"]
        s_with = await _make_sample(session, exp, "SAMPLE-101")
        own = await _link_file(session, org, exp, s_with, "SAMPLE-101_R1_001.fastq.gz")
        s_without = await _make_sample(session, exp, "SAMPLE-102")
        await session.commit()

        req = PipelineRunLaunchRequest(
            pipeline_key="nf-core/demo",
            experiment_id=exp.id,
            sample_ids=[s_with.id, s_without.id],
            drop_samples_without_files=True,
        )
        run = await PipelineRunService.launch_run(session, org.id, user.id, req)
        await session.commit()

        linked = (
            await session.execute(
                PipelineRunSample.__table__.select().where(PipelineRunSample.pipeline_run_id == run.id)
            )
        ).fetchall()
        sample_ids = {row.sample_id for row in linked}
        assert sample_ids == {s_with.id}, "dropped sample must not be in the run"
        assert run.input_files_json == [own.id], "run inputs must be only the survivor's own file"

    @pytest.mark.asyncio
    async def test_errors_when_all_samples_missing_even_with_drop(self, session, base):
        org, user, exp = base["org"], base["user"], base["exp"]
        s1 = await _make_sample(session, exp, "SAMPLE-101")
        s2 = await _make_sample(session, exp, "SAMPLE-102")
        await session.commit()

        req = PipelineRunLaunchRequest(
            pipeline_key="nf-core/demo",
            experiment_id=exp.id,
            sample_ids=[s1.id, s2.id],
            drop_samples_without_files=True,
        )
        with pytest.raises(ValidationError):
            await PipelineRunService.launch_run(session, org.id, user.id, req)

    @pytest.mark.asyncio
    async def test_prior_pipeline_outputs_are_not_used_as_inputs_by_default(self, session, base):
        """A sample whose only files are prior pipeline outputs counts as having
        no inputs (outputs must not be fed back in), so the launch is rejected."""
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _make_sample(session, exp, "SAMPLE-101")
        await _link_file(session, org, exp, s, "SAMPLE-101_trimmed.fastq.gz", source_type="pipeline_output")
        await session.commit()

        req = PipelineRunLaunchRequest(
            pipeline_key="nf-core/demo",
            experiment_id=exp.id,
            sample_ids=[s.id],
        )
        with pytest.raises(SamplesMissingFilesError):
            await PipelineRunService.launch_run(session, org.id, user.id, req)

    @pytest.mark.asyncio
    async def test_raw_inputs_used_outputs_excluded(self, session, base):
        """With both a raw upload and a prior output linked, only the raw upload
        is recorded as a run input by default."""
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _make_sample(session, exp, "SAMPLE-101")
        raw = await _link_file(session, org, exp, s, "SAMPLE-101_R1_001.fastq.gz", source_type="upload")
        await _link_file(session, org, exp, s, "SAMPLE-101_trimmed.fastq.gz", source_type="pipeline_output")
        await session.commit()

        req = PipelineRunLaunchRequest(pipeline_key="nf-core/demo", experiment_id=exp.id, sample_ids=[s.id])
        run = await PipelineRunService.launch_run(session, org.id, user.id, req)
        await session.commit()
        assert run.input_files_json == [raw.id], "only the raw upload should be a run input"

    @pytest.mark.asyncio
    async def test_include_derived_inputs_opts_in(self, session, base):
        """include_derived_inputs=True lets prior outputs be used as inputs."""
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _make_sample(session, exp, "SAMPLE-101")
        out = await _link_file(session, org, exp, s, "SAMPLE-101_trimmed.fastq.gz", source_type="pipeline_output")
        await session.commit()

        req = PipelineRunLaunchRequest(
            pipeline_key="nf-core/demo",
            experiment_id=exp.id,
            sample_ids=[s.id],
            include_derived_inputs=True,
        )
        run = await PipelineRunService.launch_run(session, org.id, user.id, req)
        await session.commit()
        assert run.input_files_json == [out.id]

    @pytest.mark.asyncio
    async def test_builtin_no_input_pipeline_is_exempt(self, session, base):
        org, user, exp = base["org"], base["user"], base["exp"]
        s = await _make_sample(session, exp, "SAMPLE-101")
        await session.commit()

        req = PipelineRunLaunchRequest(
            pipeline_key="bioaf-system-test",
            experiment_id=exp.id,
            sample_ids=[s.id],
        )
        run = await PipelineRunService.launch_run(session, org.id, user.id, req)
        await session.commit()
        assert run.status == "running"
