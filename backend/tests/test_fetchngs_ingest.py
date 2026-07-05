"""Post-fetch ingest for nf-core/fetchngs import-by-accession (ai_pipeline_run Phase 2).

Launching nf-core/fetchngs with a list of accessions pulls FASTQ + metadata, but the fetched
samples were not, by themselves, represented in bioAF. FetchngsIngestService closes that loop:
when a fetchngs run completes, it reads the samplesheet fetchngs wrote and creates one bioAF
Sample per fetched run on the run's experiment. These tests pin the parser, the create path, and
the safety properties (best-effort, idempotent, fetchngs-only).
"""

import pytest
from sqlalchemy import func, select

from app.models.experiment import Experiment
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample, sample_files
from app.services import sample_sheet_service
from app.services.fetchngs_ingest_service import FetchngsIngestService

# A representative nf-core/fetchngs samplesheet (subset of the real columns).
_FETCHNGS_SAMPLESHEET = (
    "sample,fastq_1,fastq_2,run_accession,experiment_accession,library_layout,scientific_name\n"
    "GSM7777_SRR1,s3://x/SRR1_1.fastq.gz,s3://x/SRR1_2.fastq.gz,SRR1,SRX1,PAIRED,Mus musculus\n"
    "GSM7777_SRR2,s3://x/SRR2.fastq.gz,,SRR2,SRX2,SINGLE,Mus musculus\n"
)

# A real-world shape (from spike-02): one input run accession expands to its experiment, whose
# sibling runs share the SAME `sample` id (the experiment accession SRX079566). fetchngs emits one
# row per run, so the `sample` column collides across rows.
_MULTIRUN_SAMPLESHEET = (
    "sample,fastq_1,fastq_2,run_accession,experiment_accession,library_layout,scientific_name\n"
    "SRX079566,gs://x/SRR390728_1.fastq.gz,gs://x/SRR390728_2.fastq.gz,SRR390728,SRX079566,PAIRED,Homo sapiens\n"
    "SRX079566,gs://x/SRR292241_1.fastq.gz,gs://x/SRR292241_2.fastq.gz,SRR292241,SRX079566,PAIRED,Homo sapiens\n"
)


class _FakeStorage:
    """A storage adapter stub exposing only read_text (what the ingest needs)."""

    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error
        self.read_uris: list[str] = []

    async def read_text(self, uri, *, encoding="utf-8"):
        self.read_uris.append(uri)
        if self._error is not None:
            raise self._error
        return self._text


async def _experiment(session, admin_user):
    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Accession import",
        owner_user_id=admin_user.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


async def _fetchngs_run(session, admin_user, exp, *, status="completed", outdir="gs://bioaf-results/run9"):
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        pipeline_name="nf-core/fetchngs",
        pipeline_version="1.12.0",
        status=status,
        parameters_json={"outdir": outdir, "accessions": ["SRR1", "SRR2"]},
        submitted_by_user_id=admin_user.id,
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


# ---- Parser (pure) ----


def test_parse_samplesheet_maps_accession_and_organism():
    samples = FetchngsIngestService.parse_samplesheet(_FETCHNGS_SAMPLESHEET)
    assert [s.external_id for s in samples] == ["GSM7777_SRR1", "GSM7777_SRR2"]
    assert all(s.organism == "Mus musculus" for s in samples)
    # Provenance: the source accessions are recorded so the imported sample is traceable.
    assert "SRR1" in (samples[0].prep_notes or "")
    assert "SRX1" in (samples[0].prep_notes or "")


def test_parse_samplesheet_leaves_vocab_fields_unset():
    """The three controlled-vocab fields are intentionally left None so the ingest cannot fail on an
    org's configured vocabulary; the user enriches later."""
    samples = FetchngsIngestService.parse_samplesheet(_FETCHNGS_SAMPLESHEET)
    s = samples[0]
    assert s.molecule_type is None
    assert s.library_prep_method is None
    assert s.library_layout is None


def test_parse_samplesheet_skips_rows_without_an_identifier():
    csv_text = "sample,run_accession,scientific_name\n,,Mus musculus\nGSM_OK,SRR9,Mus musculus\n"
    samples = FetchngsIngestService.parse_samplesheet(csv_text)
    assert [s.external_id for s in samples] == ["GSM_OK"]


def test_parse_samplesheet_falls_back_to_run_accession_for_external_id():
    csv_text = "fastq_1,run_accession,experiment_accession,scientific_name\nx.fastq.gz,SRR42,SRX42,Homo sapiens\n"
    samples = FetchngsIngestService.parse_samplesheet(csv_text)
    assert samples[0].external_id == "SRR42"
    assert samples[0].organism == "Homo sapiens"


# ---- Ingest (DB) ----


@pytest.mark.asyncio
async def test_ingest_for_run_creates_samples_on_the_experiment(session, admin_user):
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_FETCHNGS_SAMPLESHEET)

    created = await FetchngsIngestService.ingest_for_run(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )
    await session.commit()

    assert len(created) == 2
    # It read the fetchngs samplesheet under the run's outdir.
    assert storage.read_uris == ["gs://bioaf-results/run9/samplesheet/samplesheet.csv"]
    external_ids = set(
        (await session.execute(select(Sample.external_id).where(Sample.experiment_id == exp.id))).scalars()
    )
    assert external_ids == {"GSM7777_SRR1", "GSM7777_SRR2"}


@pytest.mark.asyncio
async def test_ingest_for_run_is_idempotent(session, admin_user):
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_FETCHNGS_SAMPLESHEET)

    first = await FetchngsIngestService.ingest_for_run(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )
    await session.commit()
    second = await FetchngsIngestService.ingest_for_run(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )
    await session.commit()

    assert len(first) == 2
    assert second == []  # accessions already present are skipped
    count = (
        await session.execute(select(func.count()).select_from(Sample).where(Sample.experiment_id == exp.id))
    ).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_ingest_for_run_ignores_non_fetchngs_run(session, admin_user):
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    run.pipeline_name = "nf-core/rnaseq"
    await session.flush()
    storage = _FakeStorage(text=_FETCHNGS_SAMPLESHEET)

    created = await FetchngsIngestService.ingest_for_run(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )

    assert created == []
    assert storage.read_uris == []  # never even reached for a non-fetchngs run


@pytest.mark.asyncio
async def test_ingest_for_run_survives_unreadable_samplesheet(session, admin_user):
    """A missing or unreadable samplesheet logs and returns [] rather than raising (the run is already
    completed; ingest is best-effort)."""
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(error=FileNotFoundError("no such object"))

    created = await FetchngsIngestService.ingest_for_run(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )

    assert created == []
    count = (
        await session.execute(select(func.count()).select_from(Sample).where(Sample.experiment_id == exp.id))
    ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_ingest_dedupes_runs_that_share_a_sample_id(session, admin_user):
    """Sibling runs of one experiment share the same fetchngs `sample` id (the experiment accession),
    so the samplesheet has multiple rows with an identical external_id. The ingest must create ONE
    sample, not attempt a duplicate insert that violates uq_samples_experiment_external_id and
    poisons the caller's transaction (spike-02 regression)."""
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_MULTIRUN_SAMPLESHEET)

    created = await FetchngsIngestService.ingest_for_run(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )
    # The pipeline monitor commits after ingest; a duplicate insert would make this raise.
    await session.commit()

    assert len(created) == 1
    external_ids = list(
        (await session.execute(select(Sample.external_id).where(Sample.experiment_id == exp.id))).scalars()
    )
    assert external_ids == ["SRX079566"]


# ---- FASTQ attach (D2): the fetchngs FASTQ -> sample File rows the analysis run consumes ----


async def _files_for_sample(session, sample_id) -> list[File]:
    return list(
        (
            await session.execute(
                select(File)
                .join(sample_files, File.id == sample_files.c.file_id)
                .where(sample_files.c.sample_id == sample_id)
            )
        )
        .scalars()
        .all()
    )


async def _sample_by_external(session, exp_id, external_id) -> Sample:
    return (
        await session.execute(
            select(Sample).where(Sample.experiment_id == exp_id, Sample.external_id == external_id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_attach_fastq_files_links_reads_to_matching_samples(session, admin_user):
    """The downstream nf-core/rnaseq|scrnaseq run needs each sample's FASTQ as linked File rows
    (launch_run's per-sample file gate is strict). attach_fastq_files reads the same samplesheet the
    ingest reads and registers each row's fastq_1/fastq_2 under the run's durable outdir, tagged R1/R2
    and linked to the matching sample."""
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_FETCHNGS_SAMPLESHEET)
    outdir = run.parameters_json["outdir"]

    await FetchngsIngestService.ingest_for_run(session, run, outdir=outdir, storage_adapter=storage)
    created = await FetchngsIngestService.attach_fastq_files(session, run, outdir=outdir, storage_adapter=storage)
    await session.commit()

    assert len(created) == 3  # SRR1 paired (R1+R2), SRR2 single-end (R1)
    assert all(f.source_type == "pipeline_output" and f.source_pipeline_run_id == run.id for f in created)
    assert all(f.experiment_id == exp.id and f.file_type == "fastq" for f in created)

    paired = await _files_for_sample(session, (await _sample_by_external(session, exp.id, "GSM7777_SRR1")).id)
    assert len(paired) == 2
    # Files land in the run's durable outdir/fastq/, not wherever the sheet pointed.
    assert {f.storage_uri for f in paired} == {
        f"{outdir}/fastq/SRR1_1.fastq.gz",
        f"{outdir}/fastq/SRR1_2.fastq.gz",
    }
    reads = {tag for f in paired for tag in f.tags_json if tag.startswith("read:")}
    assert reads == {"read:R1", "read:R2"}

    single = await _files_for_sample(session, (await _sample_by_external(session, exp.id, "GSM7777_SRR2")).id)
    assert len(single) == 1
    assert single[0].tags_json[0] == "read:R1"


@pytest.mark.asyncio
async def test_attach_fastq_files_is_idempotent(session, admin_user):
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_FETCHNGS_SAMPLESHEET)
    outdir = run.parameters_json["outdir"]
    await FetchngsIngestService.ingest_for_run(session, run, outdir=outdir, storage_adapter=storage)

    first = await FetchngsIngestService.attach_fastq_files(session, run, outdir=outdir, storage_adapter=storage)
    await session.commit()
    second = await FetchngsIngestService.attach_fastq_files(session, run, outdir=outdir, storage_adapter=storage)
    await session.commit()

    assert len(first) == 3
    assert second == []  # files already registered for this run are skipped
    total = (
        await session.execute(select(func.count()).select_from(File).where(File.source_pipeline_run_id == run.id))
    ).scalar_one()
    assert total == 3


@pytest.mark.asyncio
async def test_attach_links_monitor_preregistered_output_files(session, admin_user):
    """Regression (live smoke, 2026-07-05): on completion the pipeline monitor registers a fetchngs
    run's downloaded FASTQ as generic ``pipeline_output`` File rows (same run + same durable URIs, but
    untagged and unlinked). attach must REUSE and LINK those to their samples, not skip them as
    'already registered'. Skipping left every sample with no linked FASTQ, so the driver's
    ``_has_runnable_samples`` check was False and the study wrongly early-exited to ``missing_data``
    even though the fetch succeeded and 21 samples + 21 FASTQ existed."""
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_FETCHNGS_SAMPLESHEET)
    outdir = run.parameters_json["outdir"]
    await FetchngsIngestService.ingest_for_run(session, run, outdir=outdir, storage_adapter=storage)

    # Simulate the monitor's generic output-file registration: untagged, unlinked File rows at exactly
    # the durable URIs attach computes, for this run.
    for basename in ("SRR1_1.fastq.gz", "SRR1_2.fastq.gz", "SRR2.fastq.gz"):
        session.add(
            File(
                organization_id=admin_user.organization_id,
                storage_uri=f"{outdir}/fastq/{basename}",
                filename=basename,
                file_type="fastq",
                source_type="pipeline_output",
                source_pipeline_run_id=run.id,
                experiment_id=exp.id,
                uploader_user_id=admin_user.id,
                ingest_source="fetchngs",
                tags_json=[],
            )
        )
    await session.flush()

    created = await FetchngsIngestService.attach_fastq_files(session, run, outdir=outdir, storage_adapter=storage)
    await session.commit()

    # attach reuses the monitor's 3 rows (creates no duplicates)...
    assert created == []
    total = (
        await session.execute(select(func.count()).select_from(File).where(File.source_pipeline_run_id == run.id))
    ).scalar_one()
    assert total == 3
    # ...and links them to their samples (this is what _has_runnable_samples checks).
    paired = await _files_for_sample(session, (await _sample_by_external(session, exp.id, "GSM7777_SRR1")).id)
    single = await _files_for_sample(session, (await _sample_by_external(session, exp.id, "GSM7777_SRR2")).id)
    assert len(paired) == 2
    assert len(single) == 1
    # Adopted files gain read tags so the sample-sheet builder can pair mates by lane.
    assert {tag for f in paired for tag in (f.tags_json or []) if tag.startswith("read:")} == {"read:R1", "read:R2"}


@pytest.mark.asyncio
async def test_attach_fastq_files_multirun_pairs_across_lanes(session, admin_user):
    """When one accession expands to sibling runs collapsed under one sample (spike-02), each run's
    FASTQ must become a DISTINCT sample-sheet row (nf-core merges them), not overwrite a single lane.
    Verify the attached files pair into two lanes end to end via the sample-sheet builder."""
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_MULTIRUN_SAMPLESHEET)
    outdir = run.parameters_json["outdir"]
    await FetchngsIngestService.ingest_for_run(session, run, outdir=outdir, storage_adapter=storage)

    created = await FetchngsIngestService.attach_fastq_files(session, run, outdir=outdir, storage_adapter=storage)
    await session.commit()
    assert len(created) == 4  # two paired sibling runs

    sample = await _sample_by_external(session, exp.id, "SRX079566")
    sample._input_files = await _files_for_sample(session, sample.id)
    pairs = sample_sheet_service._extract_fastq_lane_pairs(sample)
    assert len(pairs) == 2
    assert all(r1 and r2 for r1, r2 in pairs)  # every lane has both mates


@pytest.mark.asyncio
async def test_attach_fastq_files_noop_for_non_fetchngs(session, admin_user):
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    run.pipeline_name = "nf-core/rnaseq"
    await session.flush()
    storage = _FakeStorage(text=_FETCHNGS_SAMPLESHEET)

    created = await FetchngsIngestService.attach_fastq_files(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )
    assert created == []
    assert storage.read_uris == []


@pytest.mark.asyncio
async def test_attach_fastq_files_survives_unreadable_samplesheet(session, admin_user):
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(error=FileNotFoundError("no such object"))

    created = await FetchngsIngestService.attach_fastq_files(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )
    assert created == []


@pytest.mark.asyncio
async def test_ingest_failure_does_not_poison_caller_transaction(session, admin_user, monkeypatch):
    """Ingest is best-effort and runs inside the shared pipeline-monitor session. If sample creation
    fails for any reason, it must return [] AND leave the caller's transaction usable, so a bad
    ingest can never wedge status sync for other runs (spike-02: the failure took down the monitor
    loop for an unrelated run)."""
    exp = await _experiment(session, admin_user)
    run = await _fetchngs_run(session, admin_user, exp)
    storage = _FakeStorage(text=_FETCHNGS_SAMPLESHEET)

    async def _boom(sess, experiment_id, user_id, samples_data):
        # Force a real, schema-enforced DB failure mid-ingest (a FK violation: no such experiment).
        # The production failure was a duplicate external_id, but that unique index lives only in a
        # migration, not on the model, so it is absent from the create_all test schema; a FK
        # violation poisons the transaction the same way and is enforced in tests.
        sess.add(Sample(experiment_id=10**9, external_id="orphan", status="registered"))
        await sess.flush()

    monkeypatch.setattr("app.services.sample_service.SampleService.bulk_create_samples", _boom)

    created = await FetchngsIngestService.ingest_for_run(
        session, run, outdir=run.parameters_json["outdir"], storage_adapter=storage
    )
    assert created == []

    # The caller's transaction must still be usable: an unrelated write commits cleanly, and the
    # failed ingest left nothing behind.
    session.add(Sample(experiment_id=exp.id, external_id="AFTER", status="registered"))
    await session.commit()
    external_ids = list(
        (await session.execute(select(Sample.external_id).where(Sample.experiment_id == exp.id))).scalars()
    )
    assert external_ids == ["AFTER"]
