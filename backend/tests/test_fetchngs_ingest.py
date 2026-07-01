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
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.services.fetchngs_ingest_service import FetchngsIngestService

# A representative nf-core/fetchngs samplesheet (subset of the real columns).
_FETCHNGS_SAMPLESHEET = (
    "sample,fastq_1,fastq_2,run_accession,experiment_accession,library_layout,scientific_name\n"
    "GSM7777_SRR1,s3://x/SRR1_1.fastq.gz,s3://x/SRR1_2.fastq.gz,SRR1,SRX1,PAIRED,Mus musculus\n"
    "GSM7777_SRR2,s3://x/SRR2.fastq.gz,,SRR2,SRX2,SINGLE,Mus musculus\n"
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
