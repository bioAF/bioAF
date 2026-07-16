"""Post-fetch ingest for nf-core/fetchngs import-by-accession (ai_pipeline_run Phase 2).

Launching nf-core/fetchngs with a list of accessions (the assistant's import-by-accession path)
pulls FASTQ + metadata from public databases, but the fetched samples are not, by themselves,
represented in bioAF. This service closes that loop: when a fetchngs run completes, it reads the
samplesheet fetchngs wrote and creates one bioAF Sample per fetched run on the run's experiment,
so the imported data is first-class (visible, characterizable, runnable).

It is best-effort and idempotent: a missing or malformed samplesheet logs a warning and never
fails the already-completed run, and accessions already present on the experiment are skipped.
Field mapping is deliberately conservative: external_id (the fetchngs sample id), organism, and an
accession-provenance note. The three controlled-vocabulary fields (molecule_type,
library_prep_method, library_layout) are left unset so the ingest can never fail on an org's
configured vocabulary; the user can enrich those afterwards (e.g. set the assay).
"""

import csv
import io
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_storage_adapter
from app.models.file import File
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.schemas.sample import SampleCreate
from app.services.file_service import FileService
from app.services.sample_service import SampleService

logger = logging.getLogger("bioaf.fetchngs_ingest")

# nf-core/fetchngs writes its resolved per-run metadata here, relative to the run's outdir.
_SAMPLESHEET_SUBPATH = "samplesheet/samplesheet.csv"

# The durable FASTQ live flat under this subdir of the run's outdir (spike-02 confirmed the layout:
# one {accession}_{1,2}.fastq.gz per mate per run).
_FASTQ_SUBDIR = "fastq"

# Candidate columns (matched case-insensitively) for the sample's external id, best first. fetchngs
# names each fetched run in the "sample" column; if that is absent we fall back to the accessions.
_EXTERNAL_ID_COLUMNS = ("sample", "run_accession", "experiment_accession", "sample_accession")
_ORGANISM_COLUMNS = ("scientific_name", "organism")
# The fetched run's descriptive title + library strategy from the ENA/GEO metadata fetchngs pulls.
# Captured into prep_notes so downstream sheet generation can tell a ChIP-seq control/input from an IP
# sample (lit_validation Phase 4). Best-first; different fetchngs versions spell these differently.
_TITLE_COLUMNS = ("experiment_title", "sample_title", "title", "experiment_alias", "sample_alias")
_STRATEGY_COLUMNS = ("library_strategy", "assay")
# fetchngs samplesheet FASTQ columns, in read order. fastq_2 is empty for single-end runs.
_FASTQ_COLUMNS = (("fastq_1", "R1"), ("fastq_2", "R2"))


def _is_fetchngs(run: PipelineRun) -> bool:
    return "fetchngs" in (run.pipeline_name or "").lower()


def _first(row: dict, columns: tuple[str, ...]) -> str:
    """First non-empty value among the given (already lowercased) column names."""
    for column in columns:
        value = (row.get(column) or "").strip()
        if value:
            return value
    return ""


def _samplesheet_uri(outdir: str) -> str | None:
    if not outdir:
        return None
    return f"{outdir.rstrip('/')}/{_SAMPLESHEET_SUBPATH}"


def _fastq_storage_uri(outdir: str, sheet_value: str) -> str:
    """The durable object-store URI for one FASTQ, in the run's outdir.

    fetchngs's samplesheet may point fastq_1/fastq_2 at wherever it staged the download (a work dir);
    the durable copy bioAF keeps lives at ``{outdir}/fastq/{basename}``. We take the basename and
    re-anchor it there so a downstream run reads the persisted data, not a transient work path."""
    basename = sheet_value.rsplit("/", 1)[-1]
    return f"{outdir.rstrip('/')}/{_FASTQ_SUBDIR}/{basename}"


def _row_md5(row: dict, mate: str) -> str | None:
    """Best-effort md5 for a mate ('1'|'2') across the column spellings fetchngs has used."""
    for column in (f"md5_{mate}", f"fastq_{mate}_md5"):
        value = (row.get(column) or "").strip()
        if value:
            return value
    return None


class FetchngsIngestService:
    @staticmethod
    def parse_samplesheet(csv_text: str) -> list[SampleCreate]:
        """Parse a fetchngs samplesheet into SampleCreate rows (pure; no DB access).

        Each row with a usable identifier becomes one SampleCreate carrying external_id, organism,
        and an accession-provenance note. Vocab-controlled fields are intentionally left unset (see
        the module docstring)."""
        samples: list[SampleCreate] = []
        for raw_row in csv.DictReader(io.StringIO(csv_text)):
            row = {(k or "").strip().lower(): v for k, v in raw_row.items()}
            external_id = _first(row, _EXTERNAL_ID_COLUMNS)
            if not external_id:
                continue
            organism = _first(row, _ORGANISM_COLUMNS) or None
            run_accession = (row.get("run_accession") or "").strip()
            experiment_accession = (row.get("experiment_accession") or "").strip()
            provenance = ["Imported by accession via nf-core/fetchngs."]
            if run_accession:
                provenance.append(f"run_accession={run_accession}")
            if experiment_accession:
                provenance.append(f"experiment_accession={experiment_accession}")
            # Carry the ENA/GEO title + strategy so a ChIP-seq control/input can be distinguished from
            # an IP sample at sheet-generation time (the accessions alone don't reveal it).
            strategy = _first(row, _STRATEGY_COLUMNS)
            if strategy:
                provenance.append(f"strategy={strategy}")
            title = _first(row, _TITLE_COLUMNS)
            if title:
                provenance.append(f"title={title}")
            samples.append(
                SampleCreate(
                    external_id=external_id,
                    organism=organism,
                    prep_notes=" ".join(provenance),
                )
            )
        return samples

    @staticmethod
    async def ingest_for_run(
        session: AsyncSession,
        run: PipelineRun,
        *,
        outdir: str,
        storage_adapter=None,
    ) -> list[Sample]:
        """Read a completed fetchngs run's samplesheet from ``outdir`` and create the fetched samples
        on the run's experiment. Best-effort: returns [] (and logs) on any problem rather than
        raising, so it can never break an already-completed run. Idempotent: accessions already on
        the experiment are skipped. A non-fetchngs run, or one with no experiment, is a no-op."""
        if not _is_fetchngs(run) or not run.experiment_id:
            return []

        uri = _samplesheet_uri(outdir)
        if uri is None:
            logger.warning("fetchngs run %d has no outdir; cannot ingest samples", run.id)
            return []

        storage_adapter = storage_adapter or get_storage_adapter()
        try:
            csv_text = await storage_adapter.read_text(uri)
        except Exception as exc:
            logger.warning("fetchngs run %d: could not read samplesheet at %s: %s", run.id, uri, exc)
            return []

        parsed = FetchngsIngestService.parse_samplesheet(csv_text)
        if not parsed:
            logger.info("fetchngs run %d: samplesheet had no usable rows", run.id)
            return []

        # Idempotency + within-batch dedupe: skip accessions already on this experiment, AND collapse
        # rows that repeat an external_id within this one samplesheet. fetchngs emits one row per run,
        # and sibling runs of the same experiment share the same `sample` id (the experiment
        # accession), so without the within-batch dedupe we would attempt two inserts with the same
        # external_id and violate uq_samples_experiment_external_id (spike-02).
        existing = set(
            (
                await session.execute(select(Sample.external_id).where(Sample.experiment_id == run.experiment_id))
            ).scalars()
        )
        fresh: list[SampleCreate] = []
        seen: set[str] = set()
        for candidate in parsed:
            if candidate.external_id in existing or candidate.external_id in seen:
                continue
            seen.add(candidate.external_id)
            fresh.append(candidate)
        if not fresh:
            return []

        # Create inside a SAVEPOINT so a failure here can never poison the caller's transaction. This
        # ingest is best-effort and runs inside the shared pipeline-monitor session; an uncontained
        # failure previously wedged the monitor's flush for ALL active runs, not just this one.
        try:
            async with session.begin_nested():
                created = await SampleService.bulk_create_samples(
                    session, run.experiment_id, run.submitted_by_user_id, fresh
                )
        except Exception as exc:
            logger.warning("fetchngs run %d: sample ingest failed: %s", run.id, exc)
            return []

        logger.info(
            "fetchngs run %d: ingested %d samples into experiment %d",
            run.id,
            len(created),
            run.experiment_id,
        )
        return created

    @staticmethod
    async def attach_fastq_files(
        session: AsyncSession,
        run: PipelineRun,
        *,
        outdir: str,
        storage_adapter=None,
    ) -> list[File]:
        """Register a completed fetchngs run's downloaded FASTQ as File rows linked to its samples.

        The ingest (``ingest_for_run``) creates the Sample rows but no files; a downstream
        nf-core/rnaseq|scrnaseq run cannot launch without each sample's input files (the launch path's
        per-sample FASTQ gate is strict). This closes that gap: it reads the same samplesheet and, per
        row, registers fastq_1/fastq_2 under the run's durable ``{outdir}/fastq/`` as
        ``pipeline_output`` files, tagged ``read:R1|R2`` and ``lane:NNN`` (a distinct lane per source
        run so sibling runs collapsed under one sample become separate, mergeable sample-sheet rows),
        and links each to the sample whose external_id matches the row.

        Best-effort and idempotent, exactly like ``ingest_for_run``: a non-fetchngs run, a missing
        experiment, or an unreadable samplesheet is a no-op returning ``[]``; files already registered
        for this run are skipped; and creation runs inside a SAVEPOINT so a failure can never poison
        the caller's (pipeline-monitor) transaction."""
        if not _is_fetchngs(run) or not run.experiment_id:
            return []

        uri = _samplesheet_uri(outdir)
        if uri is None:
            logger.warning("fetchngs run %d has no outdir; cannot attach FASTQ", run.id)
            return []

        storage_adapter = storage_adapter or get_storage_adapter()
        try:
            csv_text = await storage_adapter.read_text(uri)
        except Exception as exc:
            logger.warning("fetchngs run %d: could not read samplesheet at %s: %s", run.id, uri, exc)
            return []

        samples_by_external = {
            s.external_id: s
            for s in (
                await session.execute(select(Sample).where(Sample.experiment_id == run.experiment_id))
            ).scalars()
            if s.external_id
        }
        if not samples_by_external:
            return []

        # Existing run-scoped FASTQ File rows, keyed by durable URI. A row at a URI is either one a
        # prior attach tick created, OR a generic ``pipeline_output`` row the pipeline monitor
        # registered for the run's downloaded FASTQ on completion (same run + same URIs, but with no
        # sample link and no read/lane tags). We must REUSE and LINK those, not skip them: skipping left
        # every sample with no linked FASTQ, so the driver's per-sample gate saw "no runnable samples"
        # and the study wrongly early-exited to missing_data even though the fetch succeeded.
        existing_by_uri: dict[str, File] = {
            f.storage_uri: f
            for f in (
                await session.execute(
                    select(File).where(
                        File.experiment_id == run.experiment_id,
                        File.source_pipeline_run_id == run.id,
                    )
                )
            ).scalars()
        }

        lane_for: dict[str, str] = {}
        created: list[File] = []
        linked = 0
        try:
            async with session.begin_nested():
                for raw_row in csv.DictReader(io.StringIO(csv_text)):
                    row = {(k or "").strip().lower(): v for k, v in raw_row.items()}
                    sample = samples_by_external.get(_first(row, _EXTERNAL_ID_COLUMNS))
                    if sample is None:
                        continue
                    # One lane per source run so a sample's sibling runs stay distinct (mergeable) rows.
                    run_key = (row.get("run_accession") or _first(row, _EXTERNAL_ID_COLUMNS)).strip()
                    lane = lane_for.setdefault(run_key, f"{len(lane_for) + 1:03d}")
                    for column, read in _FASTQ_COLUMNS:
                        value = (row.get(column) or "").strip()
                        if not value:
                            continue
                        storage_uri = _fastq_storage_uri(outdir, value)
                        f = existing_by_uri.get(storage_uri)
                        if f is None:
                            f = File(
                                organization_id=run.organization_id,
                                storage_uri=storage_uri,
                                filename=storage_uri.rsplit("/", 1)[-1],
                                file_type="fastq",
                                source_type="pipeline_output",
                                source_pipeline_run_id=run.id,
                                experiment_id=run.experiment_id,
                                uploader_user_id=run.submitted_by_user_id,
                                ingest_source="fetchngs",
                                tags_json=[f"read:{read}", f"lane:{lane}"],
                                md5_checksum=_row_md5(row, read[-1]),
                            )
                            session.add(f)
                            await session.flush()
                            existing_by_uri[storage_uri] = f
                            created.append(f)
                        elif not (f.tags_json or []):
                            # Adopt a monitor-registered generic output row: give it the read/lane tags
                            # the sample-sheet builder needs to pair mates by lane.
                            f.tags_json = [f"read:{read}", f"lane:{lane}"]
                        # Idempotent (ON CONFLICT DO NOTHING); safe for reused rows and re-run ticks.
                        await FileService.link_file_to_sample(session, f.id, sample.id)
                        linked += 1
        except Exception as exc:
            logger.warning("fetchngs run %d: FASTQ attach failed: %s", run.id, exc)
            return []

        logger.info(
            "fetchngs run %d: attached FASTQ to samples (%d new file rows, %d sample-file links)",
            run.id, len(created), linked,
        )
        return created
