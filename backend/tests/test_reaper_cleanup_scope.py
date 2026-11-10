"""Both reapers delete exactly their run's data, through real storage results, or nothing at all.

Two coupled defects made the reapers inert, and fixing either alone would have been worse:

- they read ``obj.uri``, but ``StoredObject`` has ``storage_uri``; the AttributeError was caught,
  so no work directory was ever deleted (their doubles exposed ``uri``, which hid it);
- they listed ``nextflow-work/run-7`` without a trailing separator, which also matches runs 70,
  71 and 700. Fixing only the attribute would have deleted the neighbours' work.

And the validation reaper deleted every File registered against the fetch run before checking
where it pointed. These tests use the real local storage adapter on a temporary root, so every
listing returns real ``StoredObject`` values and every deletion is a real file disappearing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.storage import gcs as gcs_module
from app.adapters.storage.gcs import GcsStorageProvider
from app.models.component import PlatformConfig
from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample, sample_files
from app.platform.work_dir_layout import work_dir_key
from app.services.validation_fetch_reaper import ValidationFetchReaper
from app.services.validation_study_service import ValidationStudyService
from app.services.work_dir_reaper import WORK_DIR_RETENTION_DAYS, WorkDirReaper

RAW = "bioaf-raw-test"
RESULTS = "bioaf-results-test"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    monkeypatch.setattr(gcs_module, "LOCAL_DATA_ROOT", str(tmp_path))
    return GcsStorageProvider(org_slug="t")


async def _work_objects(storage, run_id: int, n: int = 2) -> list[str]:
    uris = [storage.build_uri(RAW, f"{work_dir_key(run_id)}/ab/task{i}.bam") for i in range(n)]
    for uri in uris:
        await storage.write_bytes(uri, b"intermediate")
    return uris


async def _all_exist(storage, uris) -> bool:
    return all([await storage.exists(u) for u in uris])


async def _none_exist(storage, uris) -> bool:
    return not any([await storage.exists(u) for u in uris])


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    exp = Experiment(organization_id=admin_user.organization_id, name="E", owner_user_id=admin_user.id)
    session.add(exp)
    await session.flush()
    session.add(PlatformConfig(key="results_bucket_name", value=RESULTS))
    await session.flush()
    return exp


def _run(admin_user, experiment, run_id, status, days_ago, **kw) -> PipelineRun:
    return PipelineRun(
        id=run_id,
        organization_id=kw.pop("organization_id", admin_user.organization_id),
        experiment_id=kw.pop("experiment_id", experiment.id),
        submitted_by_user_id=admin_user.id,
        pipeline_name=kw.pop("pipeline_name", "nf-core/rnaseq"),
        status=status,
        completed_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        **kw,
    )


# --- work_dir_reaper ------------------------------------------------------------------------


class TestWorkDirReaper:
    @pytest.mark.asyncio
    async def test_only_the_eligible_run_loses_its_work_dir(self, session, admin_user, experiment, storage):
        past = WORK_DIR_RETENTION_DAYS + 1
        session.add_all(
            [
                _run(admin_user, experiment, 7, "failed", past),
                _run(admin_user, experiment, 70, "running", past),  # live neighbour
                _run(admin_user, experiment, 71, "completed", past),  # completed neighbour
                _run(admin_user, experiment, 700, "failed", 0.1),  # inside its window
            ]
        )
        await session.flush()
        mine = await _work_objects(storage, 7)
        neighbours = [u for rid in (70, 71, 700) for u in await _work_objects(storage, rid)]

        with patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage):
            reaped = await WorkDirReaper.reap(session, raw_bucket=RAW)

        assert reaped == [7]
        assert await _none_exist(storage, mine)
        assert await _all_exist(storage, neighbours)

    @pytest.mark.asyncio
    async def test_a_run_whose_neighbours_are_also_eligible_still_takes_only_its_own(
        self, session, admin_user, experiment, storage
    ):
        past = WORK_DIR_RETENTION_DAYS + 1
        session.add_all([_run(admin_user, experiment, rid, "failed", past) for rid in (7, 70)])
        await session.flush()
        seven, seventy = await _work_objects(storage, 7), await _work_objects(storage, 70)

        deleted: list[str] = []
        real_delete = storage.delete

        async def recording_delete(uri, **kw):
            deleted.append(uri)
            await real_delete(uri, **kw)

        with (
            patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage),
            patch.object(storage, "delete", side_effect=recording_delete),
        ):
            reaped = await WorkDirReaper.reap(session, raw_bucket=RAW)

        assert sorted(reaped) == [7, 70]
        assert sorted(deleted) == sorted(seven + seventy)  # each object deleted once, by its own run


# --- validation_fetch_reaper ----------------------------------------------------------------


@pytest_asyncio.fixture
async def parked(session, admin_user, experiment, storage):
    """A study in `error` past its window whose completed fetch run 7 registered two FASTQ."""

    async def _make(*, file_uris: list[str] | None = None, run_kw: dict | None = None):
        run = _run(admin_user, experiment, 7, "completed", 5, pipeline_name="nf-core/fetchngs", **(run_kw or {}))
        session.add(run)
        await session.flush()
        out = f"gs://{RESULTS}/experiments/{experiment.id}/pipeline-runs/7"
        uris = file_uris or [f"{out}/fastq/SRX1_R1.fastq.gz", f"{out}/fastq/SRX1_R2.fastq.gz"]
        sample = Sample(experiment_id=experiment.id, external_id="SRX1", status="registered")
        session.add(sample)
        await session.flush()
        for uri in uris:
            await storage.write_bytes(uri, b"reads")
            f = File(
                organization_id=admin_user.organization_id,
                experiment_id=experiment.id,
                storage_uri=uri,
                filename=uri.rsplit("/", 1)[-1],
                file_type="fastq",
                source_type="pipeline_output",
                source_pipeline_run_id=7,
            )
            session.add(f)
            await session.flush()
            await session.execute(sample_files.insert().values(sample_id=sample.id, file_id=f.id))
        work = await _work_objects(storage, 7)
        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_doi="10.1234/x"
        )
        study.experiment_id = experiment.id
        study.data_run_id = 7
        study.state = "error"
        errored = datetime.now(timezone.utc) - timedelta(days=5)
        study.evidence_json = {"error_at": errored.isoformat(), "fetch_reap_after": errored.isoformat()}
        await session.flush()
        await session.commit()
        return study, uris, work

    return _make


async def _files(session, run_id=7):
    return list((await session.execute(select(File).where(File.source_pipeline_run_id == run_id))).scalars().all())


async def _links(session, experiment_id):
    return (
        await session.execute(
            select(sample_files.c.file_id)
            .join(Sample, Sample.id == sample_files.c.sample_id)
            .where(Sample.experiment_id == experiment_id)
        )
    ).all()


def _reaper_storage(storage):
    return patch("app.services.validation_fetch_reaper.get_storage_adapter", return_value=storage)


class TestValidationReaperScope:
    @pytest.mark.asyncio
    async def test_the_study_loses_exactly_its_fetch_and_work_dir(self, session, parked, storage, experiment):
        study, fastq, work = await parked()
        neighbour = await _work_objects(storage, 70)

        with _reaper_storage(storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket=RAW)

        assert reaped == [study.id]
        assert await _none_exist(storage, fastq + work)
        assert await _all_exist(storage, neighbour)
        assert await _files(session) == []
        assert await _links(session, experiment.id) == []
        assert study.evidence_json["fetch_reaped"]["objects"] == len(fastq) + len(work)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "foreign",
        [
            "gs://other-bucket/foreign.fastq",
            f"gs://{RESULTS}/experiments/999/pipeline-runs/7/fastq/x.fastq.gz",
            f"gs://{RESULTS}/experiments/{{exp}}/pipeline-runs/70/fastq/x.fastq.gz",
            f"gs://{RESULTS}/experiments/{{exp}}/pipeline-runs/7/../70/x.fastq.gz",
        ],
    )
    async def test_one_foreign_registered_uri_blocks_the_whole_study(
        self, session, parked, storage, experiment, foreign
    ):
        out = f"gs://{RESULTS}/experiments/{experiment.id}/pipeline-runs/7"
        foreign = foreign.format(exp=experiment.id)
        if "/../" in foreign:
            # Not writable through the adapter (it refuses traversal); a historical row can still say it.
            uris = [f"{out}/fastq/SRX1_R1.fastq.gz"]
        else:
            uris = [f"{out}/fastq/SRX1_R1.fastq.gz", foreign]
        study, written, work = await parked(file_uris=uris)
        if "/../" in foreign:
            f = File(
                organization_id=study.organization_id,
                experiment_id=experiment.id,
                storage_uri=foreign,
                filename="x.fastq.gz",
                file_type="fastq",
                source_type="pipeline_output",
                source_pipeline_run_id=7,
            )
            session.add(f)
            await session.commit()

        with _reaper_storage(storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket=RAW)

        assert reaped == []
        assert await _all_exist(storage, written + work)  # not even the safe ones
        assert len(await _files(session)) == len(uris) + (1 if "/../" in foreign else 0)
        assert len(await _links(session, experiment.id)) == len(written)
        evidence = study.evidence_json
        assert "fetch_reaped" not in evidence
        assert foreign in evidence["fetch_reap_blocked"]["uris"]

    @pytest.mark.asyncio
    async def test_a_fetch_run_of_another_organization_blocks_the_study(self, session, parked, storage, experiment):
        from app.models.organization import Organization

        other = Organization(name="Other", setup_complete=True)
        session.add(other)
        await session.flush()
        study, fastq, work = await parked(run_kw={"organization_id": other.id})

        with _reaper_storage(storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket=RAW)

        assert reaped == []
        assert await _all_exist(storage, fastq + work)
        assert study.evidence_json["fetch_reap_blocked"]["reason"]

    @pytest.mark.asyncio
    async def test_a_fetch_run_recording_a_foreign_outdir_blocks_the_study(self, session, parked, storage):
        study, fastq, work = await parked(run_kw={"parameters_json": {"outdir": "gs://other-bucket/"}})

        with _reaper_storage(storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket=RAW)

        assert reaped == []
        assert await _all_exist(storage, fastq + work)
        assert "fetch_reap_blocked" in study.evidence_json

    @pytest.mark.asyncio
    async def test_a_blocked_study_does_not_stop_an_unrelated_safe_one(self, session, parked, storage, admin_user):
        blocked, _fq, _w = await parked(file_uris=["gs://other-bucket/foreign.fastq"])
        exp2 = Experiment(organization_id=admin_user.organization_id, name="E2", owner_user_id=admin_user.id)
        session.add(exp2)
        await session.flush()
        session.add(_run(admin_user, exp2, 8, "completed", 5, pipeline_name="nf-core/fetchngs"))
        study2 = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_doi="10.1234/y"
        )
        study2.experiment_id, study2.data_run_id, study2.state = exp2.id, 8, "error"
        errored = datetime.now(timezone.utc) - timedelta(days=5)
        study2.evidence_json = {"fetch_reap_after": errored.isoformat()}
        work8 = await _work_objects(storage, 8)
        await session.commit()

        with _reaper_storage(storage):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket=RAW)

        assert reaped == [study2.id]
        assert await _none_exist(storage, work8)


class TestValidationReaperRecovery:
    @pytest.mark.asyncio
    async def test_failure_before_any_deletion_changes_nothing(self, session, parked, storage, experiment):
        study, fastq, work = await parked()

        async def broken(*a, **k):
            raise RuntimeError("listing unavailable")

        with _reaper_storage(storage), patch.object(storage, "list_objects", side_effect=broken):
            reaped = await ValidationFetchReaper.reap(session, raw_bucket=RAW)

        assert reaped == []
        assert await _all_exist(storage, fastq + work)
        assert len(await _links(session, experiment.id)) == 2
        assert "fetch_reap_pending" not in (study.evidence_json or {})

    @pytest.mark.asyncio
    async def test_a_failed_first_deletion_leaves_the_study_whole(self, session, parked, storage, experiment):
        study, fastq, work = await parked()

        async def down(*a, **k):
            raise RuntimeError("storage went away")

        with _reaper_storage(storage), patch.object(storage, "delete", side_effect=down):
            assert await ValidationFetchReaper.reap(session, raw_bucket=RAW) == []
        assert await _all_exist(storage, fastq + work)
        assert len(await _links(session, experiment.id)) == 2
        assert "fetch_reap_pending" not in (study.evidence_json or {})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fail_after", [1, 3])
    async def test_a_partial_deletion_unlinks_first_and_the_next_pass_finishes(
        self, session, parked, storage, experiment, admin_user, fail_after
    ):
        """fail_after=1: one FASTQ gone; 3: both FASTQ and one work object gone. The links were removed
        and committed before the first deletion, so they stay removed."""
        study, fastq, work = await parked()
        real_delete = storage.delete
        calls = {"n": 0}

        async def flaky(uri, **kw):
            if calls["n"] >= fail_after:
                raise RuntimeError("storage went away")
            calls["n"] += 1
            await real_delete(uri, **kw)

        with _reaper_storage(storage), patch.object(storage, "delete", side_effect=flaky):
            assert await ValidationFetchReaper.reap(session, raw_bucket=RAW) == []

        # Truthful intermediate state: links gone (a retry cannot treat the FASTQ as runnable),
        # rows kept (they name what is left to delete), no completion marker.
        assert await _links(session, experiment.id) == []
        assert len(await _files(session)) == 2
        evidence = study.evidence_json
        assert "fetch_reaped" not in evidence
        assert evidence["fetch_reap_pending"]["run_id"] == 7

        # A retry in between lands at the approval gate, not at setup against deleted data.
        retried = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)
        assert retried.state == "plan_ready"
        await session.commit()

        # The next pass completes the obligation even though the study left `error`.
        with _reaper_storage(storage):
            assert await ValidationFetchReaper.reap(session, raw_bucket=RAW) == [study.id]
        assert await _none_exist(storage, fastq + work)
        assert await _files(session) == []
        assert "fetch_reap_pending" not in study.evidence_json
        assert study.evidence_json["fetch_reaped"]["run_id"] == 7

    @pytest.mark.asyncio
    async def test_a_failure_in_the_database_cleanup_is_retried(self, session, parked, storage):
        study, fastq, work = await parked()

        async def broken(*a, **k):
            raise RuntimeError("db hiccup")

        with _reaper_storage(storage), patch.object(ValidationFetchReaper, "_forget_files", side_effect=broken):
            assert await ValidationFetchReaper.reap(session, raw_bucket=RAW) == []
        assert await _none_exist(storage, fastq + work)
        assert len(await _files(session)) == 2
        assert "fetch_reaped" not in study.evidence_json

        with _reaper_storage(storage):
            assert await ValidationFetchReaper.reap(session, raw_bucket=RAW) == [study.id]
        assert await _files(session) == []
