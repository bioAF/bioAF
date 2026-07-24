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

import logging

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
from app.services.validation_classifier_service import classify_study
from app.services.validation_concordance_service import compare_gene_sets, compare_interval_sets
from app.services.validation_extraction_service import ValidationExtractionService
from app.services.validation_level3_service import build_level3_inputs
from app.services.validation_study_service import ValidationStudyService

logger = logging.getLogger("bioaf.validation_driver")

# States the background driver owns. `plan_ready` is the human's (C1 gate advances it to
# acquiring_data); terminals and the pre-approval states are left alone. `comparing` is included so the
# driver runs the automatic classifier (E2/E3/E4) once; a clean `validated` auto-finalizes, everything
# else is left AT `comparing` with a suggested verdict for a human to ratify (the hybrid policy).
_ACTIVE_BACK_HALF_STATES = ("acquiring_data", "setup", "running", "extracting", "reproducing", "comparing")

_FETCHNGS_KEY = "nf-core/fetchngs"
# fetchngs's catalog default download_method is aspera, unproven on our GKE nodes; ftp is proven
# (spike-02). Pin ftp until aspera is validated on-cluster.
_FETCHNGS_DOWNLOAD_METHOD = "ftp"

_RUN_DONE = "completed"
_RUN_FAILED = {"failed", "cancelled", "error"}


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
            return await ValidationDriverService._launch_fetchngs(session, study)

        run = await ValidationDriverService._load_run(session, study.data_run_id)
        if run is None or run.status in _RUN_FAILED:
            return await ValidationDriverService._fail(session, study, "data acquisition run failed")
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

        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "setup"
        )
        return True

    @staticmethod
    async def _handle_setup(session: AsyncSession, study: ValidationStudy) -> bool:
        """Launch the analysis pipeline (D3) against the set-up experiment and advance to running."""
        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        if plan is None or not plan.pipeline_key:
            return await ValidationDriverService._fail(session, study, "no pipeline in the approved plan")

        launch = PipelineRunLaunchRequest(
            pipeline_key=plan.pipeline_key,
            experiment_id=study.experiment_id,
            parameters=dict(plan.parameters_json or {}),
            reference_genome=plan.reference_genome,
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
        study.evidence_json = evidence

        # Assemble the Level-3 inputs now that the analysis run produced the count matrix (B2e design +
        # B4 confirmed finding claim + the matrix file + the matching template). A study with no
        # confirmed differential finding gets None here and stays Level-2, unchanged. Pre-set inputs
        # (tests / a future approval-time path) are respected and not rebuilt.
        if not evidence.get("level3"):
            level3 = await build_level3_inputs(session, study, plan)
            if level3:
                evidence["level3"] = level3
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
            study.evidence_json = evidence
            if cs.status == "failed":
                return await ValidationDriverService._fail(session, study, "differential reproduction failed to launch")
            await session.flush()
            return True

        cs = await ValidationDriverService._load_compute_session(session, sid)
        if cs is None:
            return await ValidationDriverService._fail(session, study, "reproduction session missing")
        cs = await NotebookExecutionService.poll_execution(session, cs)
        if cs.status == "failed":
            return await ValidationDriverService._fail(session, study, "differential reproduction failed")
        if cs.status != "completed":
            return False  # still running

        our_fs = await ValidationDriverService._extract_reproduced_set(session, cs, level3.get("kind", "gene"))
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
        result = classify_study(
            evidence.get("comparison_targets") or [],
            evidence.get("computed_metrics") or {},
            mapping_confidence=plan.mapping_confidence if plan else None,
            reference_genome=plan.reference_genome if plan else None,
            concordance_results=[concordance] if concordance else None,
        )
        evidence["classification_result"] = result
        study.evidence_json = evidence

        if result["auto_finalize"]:
            await ValidationStudyService.transition(
                session,
                study.id,
                study.organization_id,
                study.requested_by_user_id,
                "classified",
                classification=result["classification"],
            )
        else:
            # Persist the suggested verdict; leave the study at comparing for the human gate.
            await session.flush()
        return True

    # ---- helpers ----

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
    async def _extract_reproduced_set(session: AsyncSession, cs, kind: str) -> FindingSet:
        """Read the normalized result table the differential notebook wrote (a registered output
        File) and normalize it into OUR FindingSet. Live seam (reads object storage); mocked in
        unit tests."""
        text = await ValidationDriverService._read_reproduction_output(session, cs)
        if not text:
            ns = "interval" if kind == "interval" else "unknown"
            return FindingSet(kind=kind, namespace=ns, parse_notes=["no reproduction output found"])
        if kind == "interval":
            return normalize_interval_table(text)
        return normalize_gene_table(text)

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
            await session.flush()
