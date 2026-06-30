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
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.schemas.sample import SampleCreate
from app.services.sample_service import SampleService

logger = logging.getLogger("bioaf.fetchngs_ingest")

# nf-core/fetchngs writes its resolved per-run metadata here, relative to the run's outdir.
_SAMPLESHEET_SUBPATH = "samplesheet/samplesheet.csv"

# Candidate columns (matched case-insensitively) for the sample's external id, best first. fetchngs
# names each fetched run in the "sample" column; if that is absent we fall back to the accessions.
_EXTERNAL_ID_COLUMNS = ("sample", "run_accession", "experiment_accession", "sample_accession")
_ORGANISM_COLUMNS = ("scientific_name", "organism")


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

        # Idempotency: skip accessions already recorded on this experiment.
        existing = set(
            (
                await session.execute(select(Sample.external_id).where(Sample.experiment_id == run.experiment_id))
            ).scalars()
        )
        fresh = [s for s in parsed if s.external_id not in existing]
        if not fresh:
            return []

        try:
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
