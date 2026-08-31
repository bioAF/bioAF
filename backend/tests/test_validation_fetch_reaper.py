"""Free the data of a validation study nobody came back to retry.

A study in `error` keeps everything it downloaded so a retry can reuse it. Study 11 has held
**122 GB** since 2026-08-25 that way. Nothing deleted it: `work_dir_reaper` only reaps runs that
themselves failed, and the fetch is a *completed* run publishing to the results bucket, so neither
its published FASTQ nor its work dir were ever in scope. Bucket lifecycle rules only re-class to
NEARLINE; they never delete.

Deleting the objects is only half of it. `retry_study` decides where to resume from a DB query
(`_has_runnable_samples`), so a reap that leaves the rows behind sends the retry to `setup` to
relaunch against files that are gone. The rows and the objects go together, and the study lands back
at the approval gate where a human decides whether to pay for the download again.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.file import File
from app.models.sample import Sample, sample_files
from app.services.validation_fetch_reaper import ValidationFetchReaper
from app.services.validation_study_service import ValidationStudyService


@pytest_asyncio.fixture
async def parked_study(session, admin_user):
    """A study stopped in `error` whose fetch completed: the shape the retry window exists for."""
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun

    async def _make(*, days_since_error: float = 5.0, stamped: bool = True, state: str = "error"):
        org_id = admin_user.organization_id
        exp = Experiment(
            organization_id=org_id, name="Reproduction: 10.1234/x", owner_user_id=admin_user.id, status="processing"
        )
        session.add(exp)
        await session.flush()

        fetch = PipelineRun(
            organization_id=org_id,
            experiment_id=exp.id,
            submitted_by_user_id=admin_user.id,
            pipeline_name="nf-core/fetchngs",
            status="completed",
            compute_job_ref="bioaf-fetch-1",
            completed_at=datetime.now(timezone.utc) - timedelta(days=days_since_error),
        )
        session.add(fetch)
        await session.flush()

        sample = Sample(experiment_id=exp.id, external_id="SRX1", status="registered")
        session.add(sample)
        await session.flush()
        for read in ("R1", "R2"):
            f = File(
                organization_id=org_id,
                experiment_id=exp.id,
                storage_uri=f"gs://results/experiments/{exp.id}/pipeline-runs/{fetch.id}/fastq/SRX1_{read}.fastq.gz",
                filename=f"SRX1_{read}.fastq.gz",
                file_type="fastq",
                source_type="pipeline_output",
                source_pipeline_run_id=fetch.id,
            )
            session.add(f)
            await session.flush()
            await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))

        study = await ValidationStudyService.create_study(session, org_id, admin_user.id, source_doi="10.1234/x")
        study.experiment_id = exp.id
        study.data_run_id = fetch.id
        study.state = state
        if stamped:
            errored = datetime.now(timezone.utc) - timedelta(days=days_since_error)
            study.evidence_json = {
                "error_at": errored.isoformat(),
                "fetch_reap_after": (errored + timedelta(days=3)).isoformat(),
                "computed_metrics": {"percent_aligned": 91.2},
            }
        await session.flush()
        return study

    return _make


def _storage(objects: int = 2):
    adapter = MagicMock()
    adapter.list_objects = AsyncMock(
        return_value=[MagicMock(uri=f"gs://raw/nextflow-work/run-1/{i}") for i in range(objects)]
    )
    adapter.delete = AsyncMock()
    adapter.build_uri = MagicMock(side_effect=lambda bucket, key: f"gs://{bucket}/{key}")
    return adapter


async def _linked_file_count(session, experiment_id: int) -> int:
    rows = (
        await session.execute(
            select(File.id)
            .join(sample_files, File.id == sample_files.c.file_id)
            .join(Sample, Sample.id == sample_files.c.sample_id)
            .where(Sample.experiment_id == experiment_id)
        )
    ).all()
    return len(rows)


class TestWhatGetsReaped:
    @pytest.mark.asyncio
    async def test_a_study_past_its_deadline_loses_its_downloaded_data(self, session, parked_study):
        study = await parked_study()
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert study.id in reaped
        # Both fetched FASTQ objects, plus the work dir the reaper listed.
        deleted = {c.args[0] for c in storage.delete.await_args_list}
        assert any("SRX1_R1.fastq.gz" in uri for uri in deleted)
        assert any("SRX1_R2.fastq.gz" in uri for uri in deleted)
        assert any("nextflow-work/run-" in uri for uri in deleted)

    @pytest.mark.asyncio
    async def test_the_samples_stop_claiming_files_that_no_longer_exist(self, session, parked_study):
        """`retry_study` reads the DB, not the bucket. Rows left behind send a retry to `setup` to
        relaunch against deleted files, and there is no way back from that."""
        study = await parked_study()
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert await _linked_file_count(session, study.experiment_id) == 0

    @pytest.mark.asyncio
    async def test_the_samples_themselves_survive_as_the_experiment_s_record(self, session, parked_study):
        study = await parked_study()
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        remaining = (
            (await session.execute(select(Sample).where(Sample.experiment_id == study.experiment_id))).scalars().all()
        )
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_a_reaped_study_says_so_and_keeps_the_evidence_it_earned(self, session, parked_study):
        study = await parked_study()
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        evidence = study.evidence_json or {}
        assert evidence["fetch_reaped"]["at"]
        assert evidence["computed_metrics"] == {"percent_aligned": 91.2}

    @pytest.mark.asyncio
    async def test_a_retry_after_the_reap_returns_to_the_approval_gate(self, session, parked_study, admin_user):
        """The point of unlinking. Re-downloading spends real money, so it is a decision, not a
        side effect of clicking Retry."""
        study = await parked_study()
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")
        retried = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)

        assert retried.state == "plan_ready"


class TestWhatIsLeftAlone:
    @pytest.mark.asyncio
    async def test_a_study_inside_its_window_keeps_everything(self, session, parked_study):
        study = await parked_study(days_since_error=1.0)
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert study.id not in reaped
        storage.delete.assert_not_awaited()
        assert await _linked_file_count(session, study.experiment_id) == 2

    @pytest.mark.asyncio
    async def test_a_study_that_stopped_before_this_existed_is_never_reaped(self, session, parked_study):
        """No stamp, no proof of when the window opened. Study 11 is exactly this case and the owner
        still intends to resume it, so reaping it has to be a decision rather than a deploy."""
        study = await parked_study(days_since_error=30.0, stamped=False)
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert study.id not in reaped
        assert await _linked_file_count(session, study.experiment_id) == 2

    @pytest.mark.asyncio
    async def test_a_study_that_is_not_stopped_is_never_reaped(self, session, parked_study):
        """A running study's data is in active use, however old its last error stamp is."""
        study = await parked_study(state="running")
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert study.id not in reaped
        storage.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_study_is_not_reaped_twice(self, session, parked_study):
        study = await parked_study()
        storage = _storage()

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")
            storage.delete.reset_mock()
            second = await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert study.id not in second
        storage.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_storage_failure_leaves_the_study_to_be_reaped_again(self, session, parked_study):
        """Marking it reaped while the objects survive strands the data forever: nothing would ever
        look at that prefix again."""
        study = await parked_study()
        storage = _storage()
        storage.delete = AsyncMock(side_effect=RuntimeError("GCS unavailable"))

        with patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert study.id not in reaped
        assert "fetch_reaped" not in (study.evidence_json or {})
        assert await _linked_file_count(session, study.experiment_id) == 2
