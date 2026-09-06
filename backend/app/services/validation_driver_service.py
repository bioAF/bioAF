"""A2 orchestration driver (lit_validation).

Two halves share this service:

- **Comprehension (synchronous).** ``read_and_plan`` advances a study from ``requested`` through the
  reading stage: acquires full text (B1), runs the B2/B3 extractor, then parks at ``plan_ready`` for
  the C1 human gate or takes a reading-stage early-exit classification (no accession -> missing_data;
  no nf-core equivalent -> not_reproducible).

- **Execution (background).** ``advance_active_studies`` is a tick called from a lifespan loop (like
  pipeline-monitor and auto-run). It reacts to committed pipeline-run state and walks an approved
  study through the execution back half:

      acquiring_data -> setup -> running -> extracting -> comparing

  launching nf-core/fetchngs for the data (D1), setting up experiment + samples with their FASTQ (D2),
  launching the analysis pipeline (D3), then reading QC metrics (E1) into the evidence bundle. It
  stops at ``comparing``; Phase 1 keeps the computed-vs-claimed comparison manual (a human classifies
  by hand). The automatic comparison/attribution/classifier (E2/E3/E4) is a later phase.

Everything the back half touches (launch_run, the fetchngs ingest/attach, QC extraction) is existing
machinery; this driver is the orchestration glue that sequences it and moves the study's state.
"""

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_storage_adapter
from app.exceptions import ValidationError
from app.models.reproduction_plan import ReproductionPlan
from app.models.sample import Sample, sample_files
from app.models.validation_study import VALIDATION_STUDY_TERMINAL_STATES, ValidationStudy
from app.platform.platform_config_service import PlatformConfigService
from app.schemas.experiment import ExperimentCreate
from app.schemas.pipeline_run import PipelineRunLaunchRequest
from app.services.experiment_service import ExperimentService
from app.services.fetchngs_ingest_service import FetchngsIngestService
from app.services.literature.fulltext_service import FullTextFetchService
from app.services.notebook_execution_service import NotebookExecutionService
from app.services.qc_dashboard_service import QCDashboardService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.result_set_normalizer import FindingSet, normalize_gene_table, normalize_interval_table
from app.models.organization import Organization
from app.services import llm_provider_config_service
from app.services.llm_feature_models import FEATURE_LITERATURE_VALIDATION
from app.services.llm_provider_clients import get_client
from app.services.validation_autonomy import AUTONOMY_ASSISTED, AUTONOMY_AUTONOMOUS
from app.services.validation_classifier_service import classify_study
from app.services.validation_ratification import ratify
from app.services.validation_concordance_service import compare_gene_sets, compare_interval_sets
from app.services.validation_extraction_service import ValidationExtractionService
from app.services.validation_sample_values import sample_values_from_design
from app.services.validation_level3_service import resolve_level3
from app.services.validation_study_service import ValidationStudyService, record_study_error

logger = logging.getLogger("bioaf.validation_driver")

# Decline paths that mean Level-3 was never CONFIGURED for this study (a QC-only paper with no
# ground-truth set). Reporting those as "skipped" would report an absence as a failure.
_LEVEL3_NEVER_CONFIGURED = {"no_plan", "no_finding_claim"}

# States the background driver owns. `plan_ready` is the human's (C1 gate advances it to
# acquiring_data); terminals and the pre-approval states are left alone. `comparing` is included so the
# driver runs the automatic classifier (E2/E3/E4) once; a clean `validated` auto-finalizes, everything
# else is left AT `comparing` with a suggested verdict for a human to ratify (the hybrid policy).
_ACTIVE_BACK_HALF_STATES = (
    "acquiring_data",
    # plan_7: the deposit route's two states.
    "acquiring_processed",
    "inspecting_deposit",
    "setup",
    "running",
    "extracting",
    "reproducing",
    "comparing",
)

_FETCHNGS_KEY = "nf-core/fetchngs"
# fetchngs's catalog default download_method is aspera, unproven on our GKE nodes; ftp is proven
# (spike-02). Pin ftp until aspera is validated on-cluster.
_FETCHNGS_DOWNLOAD_METHOD = "ftp"

_RUN_DONE = "completed"
_RUN_FAILED = {"failed", "cancelled", "error"}

# Transient data-acquisition (fetchngs) auto-retry (pre-PR item, 2026-07-28). A transient external
# outage (ENA/SRA 5xx, connection-refused, timeout) used to park a study terminally in `error`,
# needing manual intervention. Instead, retry the fetch a bounded number of times with exponential
# backoff (releasing the pipeline node between attempts), staying in `acquiring_data`; a genuinely
# unavailable accession short-circuits to `missing_data`; exhausting the budget parks to `error`.
_MAX_ACQUIRE_RETRIES = 3
# Backoff before each retry (seconds): 5 min, 15 min, 45 min -> a ~65 min window before giving up.
_ACQUIRE_BACKOFF_SECONDS = (300, 900, 2700)

# High-precision "the accession is genuinely unavailable" signatures. ONLY these short-circuit a fetch
# failure to `missing_data`; every other failure (network/5xx/timeout AND anything unrecognized) is
# treated as transient and retried, so a real outage is NEVER falsely called missing_data (a wrong,
# terminal verdict on the paper). Kept tight to avoid false positives.
_PERMANENT_ACQUISITION_SIGNATURES = (
    "no records",
    "no runinfo",
    "invalid accession",
    "not a valid",
    "could not be resolved",
    "does not exist",
    "no such",
    "withdrawn",
    "suppressed",
)


def classify_acquisition_failure(failure_reason: str | None, error_message: str | None) -> str:
    """Classify a failed data-acquisition run as ``"permanent"`` (the accession is genuinely
    unavailable -> missing_data) or ``"transient"`` (retry with backoff). Conservative: only a
    high-confidence permanent signature returns permanent; everything else is transient."""
    text = f"{failure_reason or ''} {error_message or ''}".lower()
    if any(sig in text for sig in _PERMANENT_ACQUISITION_SIGNATURES):
        return "permanent"
    return "transient"


async def _deposit_bytes_fetcher(url: str) -> bytes:
    """Default byte fetcher for a deposited file. Bytes, not text: the format is decided from magic
    bytes and a text decode would destroy a spreadsheet before it could be recognised."""
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


async def _resolve_deposit_prefix(session: AsyncSession, study: ValidationStudy) -> str:
    """Where a study's deposited files are stored.

    Resolved the same way `_resolve_outdir` resolves a run's results prefix, and keyed by STUDY so
    two studies of the same accession never overwrite each other.
    """
    results_bucket = await PlatformConfigService.get(session, "results_bucket_name")
    path = f"validation-deposits/study-{study.id}"
    if results_bucket:
        return get_storage_adapter().build_uri(results_bucket, path)
    return f"/data/results/{path}"


def _now() -> datetime:
    """Current UTC time. A module-level indirection so tests can freeze/advance it if needed."""
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _early_exit_classification(plan: ReproductionPlan) -> str | None:
    """Reading-stage early exit, or None to proceed to plan_ready.

    Order matters (spec-03): a missing accession is the harder stop (no data to run at all) and is
    checked first. When there is data but no pipeline, distinguish `missing_methods` (methods too thin
    to identify an assay) from `not_reproducible` (a known assay with no nf-core equivalent), keyed off
    the mapper's marker blocker.
    """
    if not (plan.accessions_json or []):
        return "missing_data"
    if plan.pipeline_key is None:
        blockers = plan.blockers_json or []
        if any("insufficient method detail" in (b or "").lower() for b in blockers):
            return "missing_methods"
        return "not_reproducible"
    return None


async def _resolve_outdir(session: AsyncSession, run) -> str:
    """The run's durable results prefix, resolved the same way the pipeline monitor does."""
    outdir = (run.parameters_json or {}).get("outdir", "")
    if outdir:
        return outdir
    results_bucket = await PlatformConfigService.get(session, "results_bucket_name")
    if results_bucket:
        return get_storage_adapter().build_uri(
            results_bucket, f"experiments/{run.experiment_id}/pipeline-runs/{run.id}"
        )
    return f"/data/results/experiments/{run.experiment_id}/pipeline-runs/{run.id}"


async def _has_runnable_samples(session: AsyncSession, experiment_id: int) -> bool:
    """Whether the experiment has at least one sample with a linked input file (D2 succeeded)."""
    row = (
        await session.execute(
            select(Sample.id)
            .join(sample_files, Sample.id == sample_files.c.sample_id)
            .where(Sample.experiment_id == experiment_id)
            .limit(1)
        )
    ).first()
    return row is not None


# The run/experiment/sample accession tokens the fetchngs ingest writes into Sample.prep_notes.
_ACCESSION_TOKEN_RE = re.compile(r"(?:run_accession|experiment_accession|sample_accession)=(\S+)")


def _sample_accession_keys(sample: Sample) -> set[str]:
    """Every accession a runnable sample can be matched on: its minted ``external_id`` plus the
    run/experiment/sample accession tokens the fetchngs ingest wrote into ``prep_notes``. Lowercased
    for case-insensitive resolution."""
    keys: set[str] = set()
    if sample.external_id:
        keys.add(sample.external_id.strip().lower())
    for value in _ACCESSION_TOKEN_RE.findall(sample.prep_notes or ""):
        v = value.strip().lower()
        if v:
            keys.add(v)
    return keys


async def _load_runnable_samples(session: AsyncSession, experiment_id: int) -> list[Sample]:
    """The experiment's samples that have a linked input file (what becomes the analysis matrix)."""
    return list(
        (
            await session.execute(
                select(Sample)
                .join(sample_files, Sample.id == sample_files.c.sample_id)
                .where(Sample.experiment_id == experiment_id)
            )
        )
        .scalars()
        .unique()
        .all()
    )


class ValidationDriverService:
    # ---- Comprehension half (synchronous, request-driven) ----

    @staticmethod
    async def read_and_plan(
        session: AsyncSession,
        study: ValidationStudy,
        full_text: str | None,
        org_id: int,
        user_id: int,
    ) -> ValidationStudy:
        """Drive a requested study to plan_ready (or an early-exit classification).

        ``full_text`` may be pasted in; when it is absent, B1 fetches the paper's full text from its
        DOI. The fetch happens BEFORE any state change so a failure leaves the study in ``requested``
        and the caller can retry (e.g. by pasting a body)."""
        if study.state != "requested":
            raise ValidationError(f"read_and_plan can only start from 'requested'; study is in '{study.state}'.")

        if not full_text:
            result = await FullTextFetchService.fetch(doi=study.source_doi)
            if result is None:
                raise ValidationError(
                    "Could not acquire full text for this study. Provide full_text, or set a source "
                    "DOI that resolves to an open-access Europe PMC article."
                )
            full_text = result.text

        # B1 full-text acquisition is the acquiring_text stage; the text is now in hand, so this
        # stage is a pass-through.
        study = await ValidationStudyService.transition(session, study.id, org_id, user_id, "acquiring_text")
        study = await ValidationStudyService.transition(session, study.id, org_id, user_id, "reading")

        plan = await ValidationExtractionService.extract(session, study, full_text, org_id, user_id)

        classification = _early_exit_classification(plan)
        if classification is not None:
            # Record the "why" (the plan's blockers) before the terminal transition.
            blockers = plan.blockers_json or []
            if blockers:
                study.failure_reason = "; ".join(blockers)
            return await ValidationStudyService.transition(
                session, study.id, org_id, user_id, "classified", classification=classification
            )

        return await ValidationStudyService.transition(session, study.id, org_id, user_id, "plan_ready")

    # ---- Execution half (background tick) ----

    @staticmethod
    async def advance_active_studies(session: AsyncSession) -> int:
        """Advance every study in an active back-half state by at most one step. Returns the number
        of studies whose state changed. Each study is handled independently and committed on its own,
        so one study's failure (recorded as a retryable ``error``) never blocks the others."""
        ids = list(
            (
                await session.execute(
                    select(ValidationStudy.id).where(ValidationStudy.state.in_(_ACTIVE_BACK_HALF_STATES))
                )
            ).scalars()
        )
        advanced = 0
        for study_id in ids:
            try:
                study = (
                    await session.execute(select(ValidationStudy).where(ValidationStudy.id == study_id))
                ).scalar_one_or_none()
                if study is None or study.state not in _ACTIVE_BACK_HALF_STATES:
                    continue
                changed = await ValidationDriverService._advance_one(session, study)
                await session.commit()
                if changed:
                    advanced += 1
            except Exception as exc:
                logger.exception("validation study %d: back-half advance failed", study_id)
                await session.rollback()
                await ValidationDriverService._mark_error(session, study_id, str(exc))
                await session.commit()
        return advanced

    @staticmethod
    async def _advance_one(session: AsyncSession, study: ValidationStudy) -> bool:
        handlers = {
            "acquiring_data": ValidationDriverService._handle_acquiring_data,
            "acquiring_processed": ValidationDriverService._handle_acquiring_processed,
            "inspecting_deposit": ValidationDriverService._handle_inspecting_deposit,
            "setup": ValidationDriverService._handle_setup,
            "running": ValidationDriverService._handle_running,
            "extracting": ValidationDriverService._handle_extracting,
            "reproducing": ValidationDriverService._handle_reproducing,
            "comparing": ValidationDriverService._handle_comparing,
        }
        handler = handlers.get(study.state)
        return await handler(session, study) if handler else False

    @staticmethod
    async def _handle_acquiring_data(session: AsyncSession, study: ValidationStudy) -> bool:
        """Launch fetchngs (first visit), or on its completion run D2 and advance to setup (or, if the
        fetched data is not usable, early-exit to missing_data per spec-02/spec-03)."""
        if study.data_run_id is None:
            # A scheduled transient-failure retry waits out its backoff before relaunching fetchngs.
            retry_at = (study.evidence_json or {}).get("acquire_retry_at")
            if retry_at and _now() < _parse_iso(retry_at):
                return False
            return await ValidationDriverService._launch_fetchngs(session, study)

        run = await ValidationDriverService._load_run(session, study.data_run_id)
        if run is None or run.status in _RUN_FAILED:
            return await ValidationDriverService._handle_acquisition_failure(session, study, run)
        if run.status != _RUN_DONE:
            return False  # still fetching

        # D2: turn the fetched data into first-class samples with their FASTQ attached. Both are
        # best-effort + idempotent, so re-running (or overlapping with the monitor's ingest) is safe.
        outdir = await _resolve_outdir(session, run)
        await FetchngsIngestService.ingest_for_run(session, run, outdir=outdir)
        await FetchngsIngestService.attach_fastq_files(session, run, outdir=outdir)

        if not await _has_runnable_samples(session, study.experiment_id):
            study.failure_reason = "fetched data was not usable (no runnable samples with FASTQ)"
            await ValidationStudyService.transition(
                session,
                study.id,
                study.organization_id,
                study.requested_by_user_id,
                "classified",
                classification="missing_data",
            )
            return True

        # Resolve the scientist's picked accessions to the real minted external_ids and rewrite the
        # design so the DE run matches the matrix columns by construction. A pick that was not fetched
        # is genuine missing data: park in samples_mismatch (zero compute) for a human to decide.
        status, reason = await ValidationDriverService._resolve_sample_design(session, study)
        if status == "mismatch":
            study.failure_reason = reason
            await ValidationStudyService.transition(
                session, study.id, study.organization_id, study.requested_by_user_id, "samples_mismatch"
            )
            return True

        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "setup"
        )
        return True

    @staticmethod
    async def _resolve_sample_design(session: AsyncSession, study: ValidationStudy) -> tuple[str, str | None]:
        """Resolve the picked accessions in the study's differential design to the real fetched
        ``Sample.external_id``s and rewrite the design in place (test/reference arms + subject keys),
        so the DE run matches the count-matrix columns by construction (spec-08 net-C).

        Returns ``("ok", None)`` when every pick resolved (or the study is QC-only with no design), or
        ``("mismatch", reason)`` when a picked sample was not fetched. In the mismatch case the design
        is still rewritten to the samples we DO have, so an override ("run with the samples we have")
        launches the reduced design cleanly."""
        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        design = (plan.differential_design_json if plan else None) or {}
        contrasts = design.get("contrasts") or []
        picked: set[str] = set()
        for contrast in contrasts:
            picked.update(contrast.get("test_samples") or [])
            picked.update(contrast.get("reference_samples") or [])
        if not picked:
            return "ok", None  # QC-only paper: no differential design to resolve

        runnable = await _load_runnable_samples(session, study.experiment_id)
        lookup: dict[str, list[str]] = {}
        for sample in runnable:
            for key in _sample_accession_keys(sample):
                bucket = lookup.setdefault(key, [])
                if sample.external_id and sample.external_id not in bucket:
                    bucket.append(sample.external_id)

        def _resolve(pick: str) -> list[str]:
            return lookup.get((pick or "").strip().lower(), [])

        def _remap(ids: list[str] | None) -> list[str]:
            out: list[str] = []
            for pick in ids or []:
                for external_id in _resolve(pick):
                    if external_id not in out:
                        out.append(external_id)
            return out

        new_contrasts = []
        for contrast in contrasts:
            new_subjects: dict[str, str] = {}
            for pick, label in (contrast.get("subjects") or {}).items():
                for external_id in _resolve(pick):
                    new_subjects[external_id] = label
            new_contrasts.append(
                {
                    **contrast,
                    "test_samples": _remap(contrast.get("test_samples")),
                    "reference_samples": _remap(contrast.get("reference_samples")),
                    "subjects": new_subjects,
                }
            )
        plan.differential_design_json = {**design, "contrasts": new_contrasts}
        await session.flush()

        unresolved = sorted({pick for pick in picked if not _resolve(pick)})
        if unresolved:
            available = sorted({s.external_id for s in runnable if s.external_id})
            reason = (
                "Held before spending compute: these picked samples were not fetched (embargoed, "
                f"withdrawn, or the download failed): {', '.join(unresolved)}. "
                f"Fetched samples available: {', '.join(available) if available else 'none'}."
            )
            return "mismatch", reason
        return "ok", None

    @staticmethod
    async def _handle_acquiring_processed(
        session: AsyncSession, study: ValidationStudy, *, fetcher=None, storage_adapter=None
    ) -> bool:
        """plan_7 step 5: download the deposited files, decode them, and land them as Files.

        No pipeline run, no Kubernetes, no notebook. This is an HTTP download, which is why the
        deposit route takes minutes where `_handle_acquiring_data` takes hours (36.7 GB / ~5h15m on
        study 22).

        A study still gets an experiment, so a deposit-route study looks like every other one in the
        UI and its files hang off the same place.
        """
        evidence = dict(study.evidence_json or {})

        # The driver ticks repeatedly; re-downloading each time would hammer NCBI and duplicate the
        # File rows.
        if evidence.get("deposit"):
            await ValidationStudyService.transition(
                session, study.id, study.organization_id, study.requested_by_user_id, "inspecting_deposit"
            )
            return True

        selection = evidence.get("deposit_selection") or {}
        wanted = list(selection.get("matrix_files") or [])
        if not wanted:
            # Assisted mode arrives here with nothing chosen yet. A wait, not a failure.
            return False

        from app.services.deposit_acquisition import (
            DepositTooLargeError,
            UnreadableDepositError,
            decode_deposit,
        )
        from app.services.file_service import FileService
        from app.services.literature.deposit_inventory_service import series_suppl_url

        base = series_suppl_url(study.source_accession or "")
        if not base:
            return await ValidationDriverService._fail(session, study, "the deposit route needs a GEO series accession")

        fetch = fetcher or _deposit_bytes_fetcher
        storage = storage_adapter or get_storage_adapter()

        metadata_name = selection.get("metadata_file")
        targets = [(n, "deposited_matrix") for n in wanted]
        if metadata_name:
            targets.append((metadata_name, "deposited_metadata"))

        if study.experiment_id is None:
            label = study.source_doi or study.source_accession or f"study {study.id}"
            experiment = await ExperimentService.create_experiment(
                session,
                study.organization_id,
                study.requested_by_user_id,
                ExperimentCreate(name=f"Reproduction: {label}"),
            )
            study.experiment_id = experiment.id

        records: list[dict] = []
        for filename, artifact_type in targets:
            url = f"{base}{filename}"
            try:
                raw = await fetch(url)
            except Exception as exc:  # noqa: BLE001
                # A partial deposit is worse than none: step 8 would build a matrix missing an arm.
                # Held on the route rather than failed, so the gate can escalate to raw reads.
                return ValidationDriverService._hold_deposit(
                    session, study, evidence, f"{filename} could not be downloaded from GEO ({exc})"
                )
            try:
                text, fmt = decode_deposit(filename, raw)
            except (UnreadableDepositError, DepositTooLargeError) as exc:
                return ValidationDriverService._hold_deposit(session, study, evidence, str(exc))

            # Stored DECODED, so step 8's notebook reads a table rather than re-deriving the format
            # from magic bytes inside R.
            uri = f"{await _resolve_deposit_prefix(session, study)}/{filename}"
            await storage.write_text(uri, text, content_type="text/tab-separated-values")
            f = await FileService.create_file_record(
                session,
                study.organization_id,
                study.requested_by_user_id,
                filename=filename,
                storage_uri=uri,
                size_bytes=len(raw),
                md5_checksum=hashlib.md5(raw).hexdigest(),
                file_type="table",
                experiment_id=study.experiment_id,
                source_type="external_deposit",
                artifact_type=artifact_type,
            )
            records.append(
                {
                    "file_id": f.id,
                    "filename": filename,
                    "url": url,
                    # GEO supplementary files can be revised in place, so the checksum of what WE
                    # downloaded is the only thing that makes this verdict reproducible later.
                    "md5": f.md5_checksum,
                    "bytes": len(raw),
                    "format": fmt,
                    "artifact_type": artifact_type,
                }
            )

        evidence.pop("deposit_failed", None)
        evidence["deposit"] = {"files": records, "fetched_at": _now().isoformat()}
        study.evidence_json = evidence
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "inspecting_deposit"
        )
        return True

    @staticmethod
    async def _handle_inspecting_deposit(
        session: AsyncSession, study: ValidationStudy, *, storage_adapter=None
    ) -> bool:
        """plan_7 step 6: measure the deposited matrix before anything is run on it.

        On the pipeline route MultiQC is what stands between a run and its verdict. A deposited
        matrix arrives with no QC, so this is where "does the pre-processed data align with what we
        expect" gets answered, in numbers, with no model involved.

        The measured `value_type` OVERRULES the one the model claimed from the filename in step 2.
        Both are kept: the claim stays on `deposit_selection` and the measurement lands on
        `deposit_inspection`, so a disagreement is visible rather than silently resolved.
        """
        from app.services.deposit_inspection import inspect_matrix

        evidence = dict(study.evidence_json or {})
        deposit = evidence.get("deposit") or {}
        matrices = [f for f in deposit.get("files") or [] if f.get("artifact_type") == "deposited_matrix"]
        if not matrices:
            return ValidationDriverService._hold_deposit(
                session, study, evidence, "no deposited matrix was acquired to inspect"
            )

        storage = storage_adapter or get_storage_adapter()
        try:
            text = await storage.read_text(matrices[0]["storage_uri"])
        except Exception as exc:  # noqa: BLE001
            return ValidationDriverService._hold_deposit(
                session, study, evidence, f"the acquired deposit could not be read back: {exc}"
            )

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        design = (plan.differential_design_json if plan else None) or {}
        design_samples: list[str] = []
        for contrast in design.get("contrasts") or []:
            design_samples.extend(contrast.get("test_samples") or [])
            design_samples.extend(contrast.get("reference_samples") or [])

        # Coverage is MEASURED here but does not gate: the design names GSM accessions and the
        # matrix names its own columns, and step 7's association is what bridges them. Gating on it
        # here would refuse every deposit before the thing that resolves it had run. The association
        # below is the real coverage gate. A transposed matrix still gates, because no association
        # can fix an axis swap.
        inspection = inspect_matrix(
            text,
            claimed_value_type=(evidence.get("deposit_selection") or {}).get("value_type"),
            design_samples=design_samples or None,
            gate_on_coverage=False,
        )
        evidence["deposit_inspection"] = inspection

        if not inspection["usable"]:
            # The study-13 lesson, enforced BEFORE the notebook rather than after it. That run
            # completed cleanly having written nothing, and the empty output was scored as a real
            # comparison of zero against the paper's 5,607.
            return ValidationDriverService._hold_deposit(
                session, study, evidence, inspection["unusable_reason"] or "the deposited matrix is not usable"
            )

        # plan_7 step 7: work out what each COLUMN is, then rewrite the design onto those columns so
        # the differential test matches its input by construction. Mirrors `_resolve_sample_design`
        # on the pipeline route, including its held-before-compute contract.
        from app.services.deposit_metadata_association import (
            associate_columns,
            parse_metadata_table,
            rewrite_design_to_columns,
        )

        metadata_rows: list[dict] = []
        meta_file = next(
            (f for f in deposit.get("files") or [] if f.get("artifact_type") == "deposited_metadata"), None
        )
        if meta_file:
            try:
                metadata_rows = parse_metadata_table(
                    await storage.read_text(meta_file["storage_uri"]),
                    column_map=(evidence.get("deposit_metadata_columns") or None),
                )
            except Exception:
                # A metadata file we cannot read is not a failure: two other sources remain, and the
                # association records which one answered.
                logger.info("validation study %d: deposited metadata unreadable; falling back", study.id)

        associations = associate_columns(
            inspection["columns"],
            metadata_rows=metadata_rows or None,
            manifest=(evidence.get("sample_manifest") or None),
        )
        evidence["deposit_metadata_association"] = associations

        if design.get("contrasts"):
            rewritten, status, reason = rewrite_design_to_columns(design, associations)
            if status == "mismatch":
                return ValidationDriverService._hold_deposit(session, study, evidence, reason or "design mismatch")
            plan.differential_design_json = rewritten
            await session.flush()

        evidence.pop("deposit_failed", None)
        study.evidence_json = evidence
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "reproducing"
        )
        return True

    @staticmethod
    def _hold_deposit(session: AsyncSession, study: ValidationStudy, evidence: dict, reason: str) -> bool:
        """Record why the deposit could not be taken and HOLD on the route.

        Not `error`: a legacy .xls or a withdrawn supplementary file is a fact about the deposit, not
        an infrastructure failure, and marking it `error` would put it in the bucket the driver is
        forbidden to retry. The gate reads this and either fixes the selection or escalates to raw
        reads, which is what the `acquiring_processed -> acquiring_data` edge is for.
        """
        evidence["deposit_failed"] = {"reason": reason, "at": _now().isoformat()}
        study.evidence_json = evidence
        logger.info("validation study %d: deposit held: %s", study.id, reason)
        return False

    @staticmethod
    async def _handle_setup(session: AsyncSession, study: ValidationStudy) -> bool:
        """Launch the analysis pipeline (D3) against the set-up experiment and advance to running."""
        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        if plan is None or not plan.pipeline_key:
            return await ValidationDriverService._fail(session, study, "no pipeline in the approved plan")

        # Imported here for the same reason `_launch` does: `pipeline_run_service` is a heavy leaf
        # of the service graph and a module-level import ties this driver's import order to it.
        from app.services.pipeline_run_service import PipelineRunService

        # Answer the pipeline's own design columns from the contrast the scientist ratified. bioAF
        # cannot derive these from a sample (cutandrun's `group`, atacseq's `replicate`), the driver
        # launches with no form to ask through, and the plan already says which arm each sample is
        # in. Anything the design does not state stays unanswered and `launch_run` still refuses.
        contract = await PipelineRunService.samplesheet_contract(
            session, study.organization_id, plan.pipeline_key, study.experiment_id
        )
        sample_values = sample_values_from_design(
            plan.differential_design_json, await _load_runnable_samples(session, study.experiment_id), contract
        )

        launch = PipelineRunLaunchRequest(
            pipeline_key=plan.pipeline_key,
            experiment_id=study.experiment_id,
            parameters=dict(plan.parameters_json or {}),
            reference_genome=plan.reference_genome,
            sample_values=sample_values,
            # The fetched FASTQ are the fetchngs run's outputs, so they are pipeline_output (derived)
            # files. launch_run's per-sample gate filters derived inputs OUT by default, which would
            # drop every fetched sample as "lacking input files"; opt in so the analysis run consumes
            # them.
            include_derived_inputs=True,
            # Some fetched samples may lack usable FASTQ; drop them rather than fail the whole run.
            drop_samples_without_files=True,
        )
        run = await ValidationDriverService._launch(session, study, launch)
        study.analysis_run_id = run.id
        if run.status in _RUN_FAILED:
            return await ValidationDriverService._fail(session, study, "analysis run failed to launch")

        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "running"
        )
        return True

    @staticmethod
    async def _handle_running(session: AsyncSession, study: ValidationStudy) -> bool:
        """Wait for the analysis run, then advance to extracting."""
        run = await ValidationDriverService._load_run(session, study.analysis_run_id)
        if run is None or run.status in _RUN_FAILED:
            return await ValidationDriverService._fail(session, study, "analysis run failed")
        if run.status != _RUN_DONE:
            return False

        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "extracting"
        )
        return True

    @staticmethod
    async def _handle_extracting(session: AsyncSession, study: ValidationStudy) -> bool:
        """Read the computed QC metrics (E1), assemble the evidence bundle (computed vs the paper's
        claimed targets), and advance to comparing for the human to classify by hand."""
        metrics: dict = {}
        dashboard_id = None
        try:
            dashboard = await QCDashboardService.get_dashboard_by_run(
                session, study.organization_id, study.analysis_run_id
            )
            if dashboard is None:
                dashboard = await QCDashboardService.generate_qc_dashboard(
                    session, study.organization_id, study.analysis_run_id
                )
            if dashboard is not None:
                metrics = dict(dashboard.metrics_json or {})
                dashboard_id = dashboard.id
        except Exception:
            # QC extraction is the evidence side, not an infra gate; a sparse/empty result is a valid
            # (and expected, per spike-00) outcome the human still classifies. Do not fail the study.
            logger.exception("validation study %d: QC extraction failed; continuing with no metrics", study.id)

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        targets = [
            {
                "metric_key": t.metric_key,
                "claimed_value": t.claimed_value,
                "unit": t.unit,
                "tolerance": t.tolerance,
                "source_locator": t.source_locator,
            }
            for t in (plan.comparison_targets if plan else [])
        ]
        # Preserve any Level-3 inputs (the `level3` block set at plan approval by B2e/B4) while
        # writing the QC evidence.
        evidence = dict(study.evidence_json or {})
        evidence.update(
            {
                "computed_metrics": metrics,
                "comparison_targets": targets,
                "data_run_id": study.data_run_id,
                "analysis_run_id": study.analysis_run_id,
                "qc_dashboard_id": dashboard_id,
            }
        )
        # Assemble the Level-3 inputs now that the analysis run produced the count matrix (B2e design +
        # B4 confirmed finding claim + the matrix file + the matching template). A study with no
        # confirmed differential finding gets None here and stays Level-2, unchanged. Pre-set inputs
        # (tests / a future approval-time path) are respected and not rebuilt.
        #
        # This runs BEFORE the single `study.evidence_json = evidence` assignment below, and that
        # ordering is load-bearing: `resolve_level3` issues SELECTs whose autoflush would flush a
        # previously-assigned evidence_json and clear its dirty flag, after which the in-place
        # `evidence["level3"] = ...` on this plain (non-Mutable) JSONB column plus a same-reference
        # reassignment goes untracked and is silently dropped -- the study would reach `reproducing`
        # with no persisted level3, and `_handle_reproducing` would fall straight through to comparing,
        # collapsing the Level-3 finding to a Level-2 verdict. Build the full evidence dict first, then
        # assign evidence_json exactly once so the reassignment is detected and persisted.
        if not evidence.get("level3"):
            decision = await resolve_level3(session, study, plan)
            if decision.inputs:
                evidence["level3"] = decision.inputs
            elif decision.reason_code not in _LEVEL3_NEVER_CONFIGURED:
                # The human confirmed a ground-truth set and something else stopped the finding step.
                # Record which, so an `inconclusive` can say a configured Level-3 did not run instead
                # of leaving the only account of it in a server log.
                evidence["level3_skipped"] = {"reason": decision.reason, "reason_code": decision.reason_code}

        study.evidence_json = evidence

        # Route to Level-3 reproduction when its inputs are present; otherwise straight to comparing
        # (Level-2 only), unchanged from before.
        next_state = "reproducing" if evidence.get("level3") else "comparing"
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, next_state
        )
        return True

    @staticmethod
    async def _handle_reproducing(session: AsyncSession, study: ValidationStudy) -> bool:
        """C3 (ADR-069): reproduce the paper's finding and score concordance (E6).

        Launch the headless differential-analysis notebook (G1) that reproduces the finding from the
        analysis run's matrix, poll it, then compare OUR result set to the paper's deposited set and
        record the concordance before advancing to comparing. If Level-3 inputs are absent, fall
        straight through to comparing (Level-2 only)."""
        evidence = dict(study.evidence_json or {})
        level3 = evidence.get("level3")
        if not level3:
            await ValidationStudyService.transition(
                session, study.id, study.organization_id, study.requested_by_user_id, "comparing"
            )
            return True

        sid = evidence.get("level3_run_session_id")
        if sid is None:
            cs = await NotebookExecutionService.execute_template(
                session,
                org_id=study.organization_id,
                user_id=study.requested_by_user_id,
                template_id=level3["template_id"],
                parameters=level3.get("parameters") or {},
                input_file_ids=level3.get("input_file_ids") or None,
                experiment_id=study.experiment_id,
            )
            evidence["level3_run_session_id"] = cs.id
            if cs.status == "failed":
                return await ValidationDriverService._degrade_to_level2(
                    session, study, evidence, "the differential reproduction notebook failed to launch"
                )
            study.evidence_json = evidence
            await session.flush()
            return True

        cs = await ValidationDriverService._load_compute_session(session, sid)
        if cs is None:
            return await ValidationDriverService._degrade_to_level2(
                session, study, evidence, "the differential reproduction session could not be found"
            )
        cs = await NotebookExecutionService.poll_execution(session, cs)
        if cs.status == "failed":
            return await ValidationDriverService._degrade_to_level2(
                session, study, evidence, "the differential reproduction notebook failed while running"
            )
        if cs.status != "completed":
            return False  # still running

        params = level3.get("parameters") or {}

        # A headless notebook that RAISED still exits its pod cleanly, so the session reports
        # `completed` with no failure_reason. Study 13 (first real ATAC-seq Level-3 attempt) aborted
        # on `stop("samples not in matrix")`, wrote nothing, and the empty result was scored as a
        # real comparison: `not_computed`, our_n 0 against the paper's 5,607. That reads as "we
        # looked and found nothing" when the truth is "the reproduction never ran".
        #
        # Checked BEFORE extraction, because an absent output is a failed reproduction rather than an
        # empty finding set, and only the failure path leaves a reason a human can act on.
        if await ValidationDriverService._read_reproduction_output(session, cs) is None:
            return await ValidationDriverService._degrade_to_level2(
                session,
                study,
                evidence,
                "the differential reproduction notebook completed but produced no output file",
            )

        our_fs = await ValidationDriverService._extract_reproduced_set(
            session,
            cs,
            level3.get("kind", "gene"),
            lfc_threshold=float(params.get("lfc_threshold", 1.0)),
            padj_threshold=float(params.get("padj_threshold", 0.05)),
        )
        paper_fs = FindingSet.from_dict(level3.get("paper_finding_set") or {})
        universe = int(
            level3.get("universe") or our_fs.n_tested or max(len(paper_fs.entities), len(our_fs.entities), 1)
        )
        if level3.get("kind") == "interval":
            conc = compare_interval_sets(paper_fs, our_fs, universe)
        else:
            conc = compare_gene_sets(paper_fs, our_fs, universe)
        evidence["level3_result"] = {"concordance": conc.to_dict(), "our_finding_set": our_fs.to_dict()}
        study.evidence_json = evidence
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "comparing"
        )
        return True

    @staticmethod
    async def _handle_comparing(session: AsyncSession, study: ValidationStudy) -> bool:
        """Run the automatic classifier (E2/E3/E4) exactly once. A clean, solid ``validated`` auto-
        finalizes (comparing -> classified); everything else stays at ``comparing`` with the suggested
        verdict recorded in evidence for a human to ratify or override (the hybrid policy)."""
        evidence = dict(study.evidence_json or {})
        if "classification_result" in evidence:
            # Already classified this study; holding at comparing for a human. Do not recompute.
            return False

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        # Fold in the Level-3 finding-concordance verdict (E6) if the reproducing step produced one.
        level3_result = evidence.get("level3_result") or {}
        concordance = level3_result.get("concordance")
        differential_attribution = None
        if concordance:
            # E3' (ADR-069): clear the DIFFERENTIAL side before a concordance divergence can strike the
            # paper. thresholds_matched: the paper stated its cutoffs AND we applied them (the reproduced
            # set is normalized with the plan's thresholds). method_comparable: our reproduction uses
            # DESeq2, the standard count-based DE/DA method for the supported RNA/ATAC/ChIP substrates,
            # so it is comparable by construction (a future refinement could compare the paper's exact tool).
            design = (plan.differential_design_json if plan else None) or {}
            th = design.get("thresholds") or {}
            differential_attribution = {
                "thresholds_matched": th.get("log2fc") is not None and th.get("padj") is not None,
                "method_comparable": True,
            }
        result = classify_study(
            evidence.get("comparison_targets") or [],
            evidence.get("computed_metrics") or {},
            mapping_confidence=plan.mapping_confidence if plan else None,
            reference_genome=plan.reference_genome if plan else None,
            concordance_results=[concordance] if concordance else None,
            differential_attribution=differential_attribution,
            # E3 per-metric attribution: the tools the PAPER named, next to the pipeline we ran, so a
            # divergence with a known tool-pair cause is explained instead of merely reported.
            paper_tools=(plan.tools_json if plan else None),
            pipeline_key=(plan.pipeline_key if plan else None),
        )
        evidence["classification_result"] = result

        # plan_6 step 8: in autonomous mode the model has the last word on what the measurements
        # MEAN. The measurement itself stays on the record either way, beside the ratification, so a
        # scientist can see both what was computed and what was concluded from it.
        ratification = await ValidationDriverService._ratify(session, study, result)
        if ratification is not None:
            evidence["ratification"] = ratification
        study.evidence_json = evidence

        finalize = ratification["finalize"] if ratification else result["auto_finalize"]
        classification = ratification["verdict"] if ratification else result["classification"]
        if finalize:
            await ValidationStudyService.transition(
                session,
                study.id,
                study.organization_id,
                study.requested_by_user_id,
                "classified",
                classification=classification,
            )
        else:
            # Persist the suggested verdict; leave the study at comparing for the human gate.
            await session.flush()
        return True

    @staticmethod
    async def _ratify(session: AsyncSession, study: ValidationStudy, result: dict) -> dict | None:
        """The model's ratification of the measured verdict, or None to keep the assisted behaviour.

        None covers assisted mode, an org with no usable provider, and a provider outage. All three
        hold the study exactly where the shipped policy holds it, which is the safe direction: a
        study finalised on a failed call would be a verdict nobody made.
        """
        org = await session.get(Organization, study.organization_id)
        autonomy = (org.lit_validation_autonomy if org else None) or AUTONOMY_ASSISTED
        if autonomy != AUTONOMY_AUTONOMOUS:
            return None

        cfg = await llm_provider_config_service.get_for_feature(
            session, study.organization_id, FEATURE_LITERATURE_VALIDATION
        )
        if cfg is None:
            logger.warning("study %s is autonomous but the org has no LLM provider; holding", study.id)
            return None

        return await ratify(
            result,
            autonomy=autonomy,
            client=get_client(cfg.provider),
            model=cfg.model,
            api_key=cfg.api_key,
        )

    # ---- helpers ----

    @staticmethod
    async def _handle_acquisition_failure(session: AsyncSession, study: ValidationStudy, run) -> bool:
        """A failed data-acquisition run: a genuinely unavailable accession -> missing_data; a transient
        outage -> a bounded backoff retry (release the node between attempts), then terminal error when
        the budget is spent. A missing run row is treated as transient (retry)."""
        reason = run.failure_reason if run is not None else None
        message = run.error_message if run is not None else "data acquisition run row not found"

        if classify_acquisition_failure(reason, message) == "permanent":
            study.failure_reason = f"data acquisition failed permanently: {reason or 'accession unavailable'}"
            await ValidationStudyService.transition(
                session,
                study.id,
                study.organization_id,
                study.requested_by_user_id,
                "classified",
                classification="missing_data",
            )
            return True

        evidence = dict(study.evidence_json or {})
        retries = int(evidence.get("acquire_retries", 0))
        if retries >= _MAX_ACQUIRE_RETRIES:
            return await ValidationDriverService._fail(
                session,
                study,
                f"data acquisition run failed after {retries} retries (transient failures did not clear)",
            )
        delay = _ACQUIRE_BACKOFF_SECONDS[min(retries, len(_ACQUIRE_BACKOFF_SECONDS) - 1)]
        evidence["acquire_retries"] = retries + 1
        evidence["acquire_retry_at"] = (_now() + timedelta(seconds=delay)).isoformat()
        # Reassign a fresh dict (evidence_json is a plain, non-Mutable JSONB column) and clear the run so
        # a later tick, once the backoff elapses, relaunches fetchngs (D1).
        study.evidence_json = evidence
        study.data_run_id = None
        logger.info(
            "validation study %d: transient data-acquisition failure, retry %d/%d scheduled in %ds",
            study.id,
            retries + 1,
            _MAX_ACQUIRE_RETRIES,
            delay,
        )
        return True

    @staticmethod
    async def _launch_fetchngs(session: AsyncSession, study: ValidationStudy) -> bool:
        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        accessions = list(plan.accessions_json or []) if plan else []
        if not accessions:
            study.failure_reason = "no accession in the approved plan"
            await ValidationStudyService.transition(
                session,
                study.id,
                study.organization_id,
                study.requested_by_user_id,
                "classified",
                classification="missing_data",
            )
            return True

        if study.experiment_id is None:
            label = study.source_doi or study.source_accession or f"study {study.id}"
            experiment = await ExperimentService.create_experiment(
                session,
                study.organization_id,
                study.requested_by_user_id,
                ExperimentCreate(name=f"Reproduction: {label}"),
            )
            study.experiment_id = experiment.id

        launch = PipelineRunLaunchRequest(
            pipeline_key=_FETCHNGS_KEY,
            experiment_id=study.experiment_id,
            parameters={"accessions": accessions, "download_method": _FETCHNGS_DOWNLOAD_METHOD},
        )
        run = await ValidationDriverService._launch(session, study, launch)
        study.data_run_id = run.id
        if run.status in _RUN_FAILED:
            return await ValidationDriverService._fail(session, study, "data acquisition run failed to launch")
        return True  # stays in acquiring_data until the fetch completes

    @staticmethod
    async def _launch(session: AsyncSession, study: ValidationStudy, launch: PipelineRunLaunchRequest):
        from app.services.pipeline_run_service import PipelineRunService

        return await PipelineRunService.launch_run(session, study.organization_id, study.requested_by_user_id, launch)

    @staticmethod
    async def _load_run(session: AsyncSession, run_id: int | None):
        if run_id is None:
            return None
        from app.services.pipeline_run_service import PipelineRunService

        return await PipelineRunService.get_run_model(session, run_id)

    @staticmethod
    async def _load_compute_session(session: AsyncSession, session_id: int):
        from app.models.notebook_session import ComputeSession

        return (
            await session.execute(select(ComputeSession).where(ComputeSession.id == session_id))
        ).scalar_one_or_none()

    @staticmethod
    async def _extract_reproduced_set(
        session: AsyncSession,
        cs,
        kind: str,
        lfc_threshold: float = 1.0,
        padj_threshold: float = 0.05,
    ) -> FindingSet:
        """Read the normalized result table the differential notebook wrote (a registered output
        File) and normalize it into OUR FindingSet. Applies the paper's captured thresholds (passed
        from the plan) so our set is defined by the same cutoffs as the paper's set (E3': a threshold
        mismatch is an our-side effect, not a real divergence). Live seam (reads object storage);
        mocked in unit tests."""
        text = await ValidationDriverService._read_reproduction_output(session, cs)
        if not text:
            ns = "interval" if kind == "interval" else "unknown"
            return FindingSet(kind=kind, namespace=ns, parse_notes=["no reproduction output found"])
        if kind == "interval":
            return normalize_interval_table(text, lfc_threshold=lfc_threshold, padj_threshold=padj_threshold)
        return normalize_gene_table(text, lfc_threshold=lfc_threshold, padj_threshold=padj_threshold)

    @staticmethod
    async def _read_reproduction_output(session: AsyncSession, cs) -> str | None:
        from app.models.file import File
        from app.models.notebook_session_file import NotebookSessionFile

        rows = list(
            (
                await session.execute(
                    select(File)
                    .join(NotebookSessionFile, NotebookSessionFile.file_id == File.id)
                    .where(NotebookSessionFile.session_id == cs.id, NotebookSessionFile.access_type == "output")
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None

        def _score(f) -> tuple[bool, bool]:
            n = (f.filename or "").lower()
            looks_like_result = any(t in n for t in ("finding", "result", "de_", "diff"))
            tabular = n.endswith((".csv", ".tsv", ".txt"))
            return (looks_like_result, tabular)

        rows.sort(key=_score, reverse=True)
        try:
            return await get_storage_adapter().read_text(rows[0].storage_uri)
        except Exception:
            logger.exception("validation study: failed to read reproduction output for session %d", cs.id)
            return None

    @staticmethod
    async def _degrade_to_level2(session: AsyncSession, study: ValidationStudy, evidence: dict, reason: str) -> bool:
        """A Level-3 failure is ADDITIVE, not destructive: keep the Level-2 verdict and say what failed.

        The study already earned a Level-2 QC verdict in ``extracting``; the Level-3 finding step is an
        attempt to add a stronger, finding-tier verdict on top of it. Routing a notebook failure through
        ``_fail`` sent the study to terminal ``error`` and threw that Level-2 evidence away, so a
        reproduction that could not run scored WORSE than one that was never configured. Record the
        reason under ``level3_failed`` and advance to ``comparing``, where the classifier produces the
        Level-2 verdict it would have produced anyway and the page states that the finding step failed.
        """
        evidence["level3_failed"] = {"reason": reason}
        study.evidence_json = evidence
        logger.info("study %d: Level-3 reproduction failed (%s); degrading to Level-2", study.id, reason)
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "comparing"
        )
        return True

    @staticmethod
    async def _fail(session: AsyncSession, study: ValidationStudy, reason: str) -> bool:
        await ValidationStudyService.transition(
            session,
            study.id,
            study.organization_id,
            study.requested_by_user_id,
            "error",
            failure_reason=reason,
        )
        return True

    @staticmethod
    async def _mark_error(session: AsyncSession, study_id: int, reason: str) -> None:
        study = (
            await session.execute(select(ValidationStudy).where(ValidationStudy.id == study_id))
        ).scalar_one_or_none()
        if study is not None and study.state not in VALIDATION_STUDY_TERMINAL_STATES:
            study.state = "error"
            study.failure_reason = (reason or "")[:2000]
            # Same stamp and same announcement as the guarded path: this one sets the state directly
            # because the handler that raised may have left an illegal transition behind, but it is
            # still a study stopping and a human still has to hear about it.
            await record_study_error(study)
            await session.flush()
