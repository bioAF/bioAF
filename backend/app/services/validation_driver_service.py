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
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
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
from app.services.validation_assessment import (
    conclude_without_execution,
    record_refusal,
    deposit_bytes_fetcher as _deposit_bytes_fetcher,
    run_assessment,
)
from app.services.validation_acquisition_outcome import (
    AWAITING_INPUT,
    INPUT_UNIDENTIFIED,
    INPUT_UNREADABLE,
    MAX_ATTEMPTS,
    NO_ADAPTER,
    NO_COMPATIBLE_CONTRAST,
    READINESS_CAUSES,
    RESOURCE_LIMIT,
    RETRIEVAL_TRANSIENT,
    SAMPLE_MAPPING_UNRESOLVED,
    acquisition_accession,
    backoff_for,
    classify_hold,
    exhausted,
    exhaustion_detail,
    outcome_for,
    retrieval_cause,
)
from app.services.contrast_selection import selected_contrast_for
from app.services.validation_route_policy import decide_route
from app.services.validation_ownership import (
    ClaimLost,
    adopt,
    adoptable,
    assert_held,
    begin_operation,
    finish_operation,
    live_claim,
    owned,
    record_dispatch,
)
from app.services.validation_provenance import record_stage
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
from app.services.validation_issue_service import ValidationIssueService
from app.services.validation_sample_values import sample_values_from_design
from app.services.validation_level3_service import resolve_level3, resolve_level3_from_deposit
from app.services.validation_study_service import ValidationStudyService, record_study_error

logger = logging.getLogger("bioaf.validation_driver")


def _accessibility_update(answer: dict | None) -> dict:
    """A capability source's accessibility fields, from what an attempted fetch established.

    Absent means this source was never reached in the ordering (an earlier one resolved), so it
    keeps `not_attempted` rather than being marked as a failure it never had.
    """
    if not answer:
        return {}
    return {"accessible": answer.get("accessible", "unknown"), "accessible_reason": answer.get("reason")}


# plan_7 step 13: each capability question in the user's language, for the issues section. A row
# that could not be established says which question went unanswered, not which key was empty.
# plan_7 step 17: where the pod writes its log. It arrives as an output file for a run that
# completed, and is read as the transcript rather than offered as a result.
_TRANSCRIPT_FILENAME = "transcript.txt"

_CAPABILITY_STEPS = {
    "deposit_exists": "checking whether this paper has a data deposit",
    "raw_data": "checking whether raw sequencing data is available",
    "preprocessed_data": "checking whether pre-processed data is available",
    "sample_metadata": "checking whether the deposit carries sample metadata",
    "code_artifact": "checking whether the paper published a code artifact",
    "code_repository": "checking whether the paper links a code repository",
}

# Decline paths that mean Level-3 was never CONFIGURED for this study (a QC-only paper with no
# ground-truth set). Reporting those as "skipped" would report an absence as a failure.
_LEVEL3_NEVER_CONFIGURED = {"no_plan", "no_finding_claim"}

# States the background driver owns. `plan_ready` is the human's (C1 gate advances it to
# acquiring_data); terminals and the pre-approval states are left alone. `comparing` is included so the
# driver runs the automatic classifier (E2/E3/E4) once; a clean `validated` auto-finalizes, everything
# else is left AT `comparing` with a suggested verdict for a human to ratify (the hybrid policy).
# The two front-half states the driver owns ONLY for a study whose route was chosen at the button.
# A study with no `intended_route` keeps the manual "Read paper" and "Approve" clicks, so every other
# entry point behaves exactly as it did.
_SELF_DRIVING_FRONT_HALF_STATES = ("requested", "plan_ready")


def _route_unavailable_reason(route: str, capabilities: dict) -> str | None:
    """Why the chosen route cannot be taken, in the reader's language, or None when it can.

    change_7.2 section 1: the judgment itself now lives in ``validation_route_policy`` so that the
    driver and the C1 gate cannot disagree. This is the wording, for callers that only want the
    sentence.
    """
    decision = decide_route(route=route, capabilities=capabilities)
    return None if decision.authorizes_execution else decision.reason


def _has_organism_source(evidence: dict) -> bool:
    """Whether any described deposit could have declared an organism.

    Only GEO is read for one, via its series matrix. Saying "the deposit declares no organism" for
    an EGA study reported a lookup that never happened.
    """
    deposits = (evidence.get("capabilities") or {}).get("deposits") or []
    return any(d.get("archive") == "geo" for d in deposits if isinstance(d, dict))


def _named_accessions(
    study: "ValidationStudy", plan, evidence: dict | None = None, *, for_discovery: bool = False
) -> list[dict]:
    """Every deposit this paper names, with where each one came from.

    change_7.1 section 1: discovery was handed ``study.source_accession`` alone, so a study
    requested by DOI arrived with an empty string and every question was answered NO. The
    accessions the reading extracted live on the plan, and a paper that deposited to EGA has a
    deposit whether or not anyone typed its accession into the request.

    The requested accession stays first and stays authoritative for what a run fetches. The
    extracted ones are reported beside it, because "the paper also deposited this" is evidence
    about the paper rather than an instruction to go and fetch it.

    change_7.5 section 1.5: for DISCOVERY, the model's whole list (a requested accession narrows what
    the plan fetches, not what the paper names) and the identifiers the text scan found are listed too;
    the model's list alone decides nothing. What an acquisition may point at is unchanged: the
    requested accession and the plan's.
    """
    named: list[dict] = []
    requested = (study.source_accession or "").strip()
    if requested:
        named.append({"accession": requested, "provenance": "requested"})
    evidence = evidence if evidence is not None else (study.evidence_json or {})
    if not for_discovery:
        evidence = {}
    extracted_lists = (getattr(plan, "accessions_json", None) or [], evidence.get("extracted_accessions") or [])
    for extracted in (a for listed in extracted_lists for a in listed):
        accession = str(extracted or "").strip()
        if accession:
            named.append({"accession": accession, "provenance": "extracted"})
    for scanned in evidence.get("scanned_identifiers") or []:
        if isinstance(scanned, dict) and scanned.get("archive") not in ("github", "gitlab"):
            named.append(
                {"accession": scanned["identifier"], "provenance": "text_scan", "archive": scanned.get("archive")}
            )
    return named


def _driver_owns(study: "ValidationStudy") -> bool:
    """Whether this loop may advance the study on this tick.

    The back half is always the driver's. The front half is only the driver's when the requester
    already chose a route at the button; otherwise `requested` waits for "Read paper" and
    `plan_ready` waits at the C1 gate, exactly as they did before.
    """
    if study.state in _ACTIVE_BACK_HALF_STATES:
        return True
    return study.state in _SELF_DRIVING_FRONT_HALF_STATES and study.intended_route is not None


# The holds a person resolves. The driver owns these states but will not move the study on until
# somebody acts, so the page must not read them as bioAF working.
_PERSON_HOLDS = ("route_blocked", "awaiting_choice", "awaiting_adoption")


def is_advancing(study: "ValidationStudy") -> bool:
    """Whether bioAF moves this study on without a person.

    Study 37: the page offered "Read paper" on a study the driver had already claimed, and the click
    came back as "Another worker is already reading this paper", naming a job nobody could see. The
    page polls while this holds and offers no click that races the driver.
    """
    if not _driver_owns(study):
        return False
    evidence = study.evidence_json or {}
    if any(evidence.get(hold) for hold in _PERSON_HOLDS):
        return False
    # The classifier runs once; after that the study waits at `comparing` for a person to ratify.
    return not (study.state == "comparing" and "classification_result" in evidence)


async def study_activity(session: AsyncSession, study: "ValidationStudy") -> dict:
    """``{"advancing", "working", "since"}``: whether bioAF moves the study on by itself, and whether
    a worker holds it right now (a live claim) and since when. An expired claim is a crashed worker,
    not work under way."""
    claim = await live_claim(session, study.id)
    return {
        "advancing": is_advancing(study),
        "working": claim is not None,
        "since": claim.claimed_at.isoformat() if claim is not None else None,
    }


# change_7.2 section 4: where the public assessment runs. Both routes' first post-approval state, so
# the assessment record exists before any acquisition is attempted and regardless of whether the
# acquisition then succeeds.
_ASSESSMENT_STATES = ("acquiring_data", "acquiring_processed")

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

# change_7.2 section 2: the logical external operations a study can have in flight. One identity
# per logical attempt, persisted before dispatch, so a restart adopts rather than relaunches.
_OP_DATA_ACQUISITION = "data_acquisition"
_OP_ANALYSIS = "analysis"

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


def _technical_detail(cause: str | None, location: str | None, *, attempts: int | None = None) -> dict | None:
    """What an administrator needs beside a limitation's plain sentence, kept out of the sentence.

    change_7.3 decision 8 and change_7.4 section 1.1: the report states the failure plainly; the
    typed cause, the location and the attempt count sit under a collapsed element.
    """
    detail = {"cause": cause, "url": location, "attempts": attempts}
    return {k: v for k, v in detail.items() if v is not None} or None


def _record_readiness(evidence: dict, value: str, reason: str | None, *, cause: str | None = None) -> None:
    """Record whether the selected analysis can run on the acquired input, where that is decided.

    change_7.4 section 1.3: acquired, usable and ready for analysis are three separate facts, and
    the report reads this one rather than inferring it from wherever the study stopped.
    """
    evidence["analysis_readiness"] = {"value": value, "reason": reason, "cause": cause, "at": _now().isoformat()}


def _readiness_statement(inputs: dict) -> str:
    """What made the input ready, in the reader's words: the contrast, its arms and the test."""
    params = inputs.get("parameters") or {}
    test = [s for s in str(params.get("test_samples") or "").split(",") if s]
    reference = [s for s in str(params.get("reference_samples") or "").split(",") if s]
    method = {"deseq2": "DESeq2", "limma_trend": "limma-trend"}.get(inputs.get("method") or "", "the analysis")
    return (
        f"{inputs.get('contrast') or 'the selected contrast'}: {len(test)} test and {len(reference)} reference "
        f"sample(s) assigned, and {method} parameters built"
    )


def _waiting_to_retry(study: ValidationStudy) -> bool:
    """Whether a transient acquisition failure is still waiting out its backoff.

    change_7.4 section 1.2: one guard, run before every state that can raise a transient hold.
    Only `_handle_acquiring_processed` checked it, so a transient hold raised during inspection
    retried on every 30-second tick and spent the whole bound in about a minute.
    """
    retry_at = (study.evidence_json or {}).get("acquisition_retry_at")
    return bool(retry_at) and _now() < _parse_iso(retry_at)


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
        *,
        claim=None,
        holder: str = "api",
    ) -> ValidationStudy:
        """Drive a requested study to plan_ready (or an early-exit classification).

        ``full_text`` may be pasted in; when it is absent, B1 fetches the paper's full text from its
        DOI. The fetch happens BEFORE any state change so a failure leaves the study in ``requested``
        and the caller can retry (e.g. by pasting a body).

        change_7.2 section 2: the study is OWNED first. Both `POST /{id}/read` and the driver called
        this on study 33 within three seconds of each other, and the `state != "requested"` guard
        below could not see the other transaction's uncommitted state, so the paper was extracted
        twice and two plans were written. ``claim`` is passed when the caller already owns the study,
        so the driver's tick does not contend with itself.
        """
        if claim is not None:
            return await ValidationDriverService._read_and_plan_owned(
                session, study, full_text, org_id, user_id, claim=claim
            )
        async with owned(session, study.id, holder=holder) as own:
            if own is None:
                raise ValidationError(
                    "Another worker is already reading this paper. Its result will appear when it finishes."
                )
            # Re-read after claiming: the state that justified the attempt may have changed while the
            # claim was contended, which is exactly what happened when two writers raced.
            await session.refresh(study)
            return await ValidationDriverService._read_and_plan_owned(
                session, study, full_text, org_id, user_id, claim=own
            )

    @staticmethod
    async def _read_and_plan_owned(
        session: AsyncSession,
        study: ValidationStudy,
        full_text: str | None,
        org_id: int,
        user_id: int,
        *,
        claim,
    ) -> ValidationStudy:
        """The read itself, performed under a claim this caller holds."""
        if study.state != "requested":
            raise ValidationError(f"read_and_plan can only start from 'requested'; study is in '{study.state}'.")

        # change_7.1 section 2: the article's supplement manifest comes from the SAME document the
        # body text does. A pasted body is not a document, so it carries none, and an empty manifest
        # there means "nobody looked" rather than "the paper published none".
        supplements: list[dict] = []
        pmcid = ""
        if not full_text:
            result = await FullTextFetchService.fetch(doi=study.source_doi)
            if result is None:
                raise ValidationError(
                    "Could not acquire full text for this study. Provide full_text, or set a source "
                    "DOI that resolves to an open-access Europe PMC article."
                )
            full_text = result.text
            supplements = result.supplements
            # Kept so the supplement bundle can be fetched later without resolving the DOI again.
            pmcid = result.external_id or ""

        # B1 full-text acquisition is the acquiring_text stage; the text is now in hand, so this
        # stage is a pass-through.
        study = await ValidationStudyService.transition(session, study.id, org_id, user_id, "acquiring_text")
        study = await ValidationStudyService.transition(session, study.id, org_id, user_id, "reading")
        record_stage(study, "reading")

        plan = await ValidationExtractionService.extract(session, study, full_text, org_id, user_id)

        evidence = dict(study.evidence_json or {})
        evidence["supplements"] = supplements
        if pmcid:
            evidence["pmcid"] = pmcid
        # change_7.5 section 1.5: what the text names, found deterministically while it is in hand.
        from app.services.resource_identifiers import scan_identifiers

        evidence["scanned_identifiers"] = scan_identifiers(full_text)
        # change_7.3 section 7: bounded passages of the paper, kept while the text is in hand. Nothing
        # of the paper reached reconciliation before, so its own exclusion statement could never
        # correct a count, and a failed download left nothing at all to reconcile against.
        evidence["paper_passages"] = await ValidationDriverService._paper_passages(session, plan, full_text)
        study.evidence_json = evidence
        await session.flush()

        # plan_7 step 13: establish what this paper actually has, BEFORE the C1 gate, so the route
        # modal offers what is available rather than three equal-looking options. Runs here rather
        # than in the driver because the gate is pre-approval: answers produced after approval
        # cannot inform the decision to approve. Two HTTP calls, no model, no compute.
        await ValidationDriverService._discover_capabilities(session, study, plan, has_full_text=bool(full_text))

        # plan_7 step 14: the cheap checks, beside step 13 and for the same reason. The gate is
        # pre-approval, so an answer produced after approval cannot inform the decision to approve.
        await ValidationDriverService._run_precompute_checks(session, study, plan, full_text=full_text)

        # The fence. A claim that expired under this work means somebody else owns the study now, and
        # landing this extraction over theirs is the duplicate the claim exists to prevent.
        try:
            await assert_held(session, claim)
        except ClaimLost:
            await session.rollback()
            raise

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

    @staticmethod
    async def _paper_passages(session: AsyncSession, plan, full_text: str | None) -> dict:
        """The passage around each claim, and the paper's exclusion statements. Bounded; never the
        full text. Targets are queried explicitly: the relationship is lazy and raises outside IO."""
        from app.models.comparison_target import ComparisonTarget
        from app.services.validation_passages import paper_passages

        claim_texts: list[str | None] = []
        if plan is not None:
            rows = (
                (
                    await session.execute(
                        select(ComparisonTarget)
                        .where(ComparisonTarget.reproduction_plan_id == plan.id)
                        .order_by(ComparisonTarget.id)
                    )
                )
                .scalars()
                .all()
            )
            claim_texts = [t.claim_text for t in rows if t.claim_text]
        return paper_passages(full_text or "", claim_texts)

    @staticmethod
    async def _discover_capabilities(
        session: AsyncSession, study: ValidationStudy, plan, *, has_full_text: bool, fetcher=None
    ) -> dict:
        """plan_7 step 13: land the phase-1 answers on ``evidence["capabilities"]``.

        Never raises and never blocks a read. Nothing it discovers rules a paper in or out; it
        decides which route is best and how high a validation level is reachable. Every UNKNOWN
        carries its own failure reason so a discovery failure is visible as a limitation of the run
        rather than as a fact about the paper.
        """
        from app.services.validation_capabilities import UNKNOWN, discover_capabilities

        try:
            capabilities = await discover_capabilities(
                accessions=_named_accessions(study, plan, for_discovery=True),
                has_full_text=has_full_text,
                code_availability=(plan.code_availability_json if plan else None),
                fetcher=fetcher,
            )
        except Exception as exc:  # noqa: BLE001 - discovery informs the gate; it cannot fail a read
            logger.warning("capability discovery failed for study %s: %s", study.id, exc)
            return {}

        evidence = dict(study.evidence_json or {})
        evidence["capabilities"] = capabilities
        study.evidence_json = evidence
        await session.flush()

        # A discovery failure is a limitation of THIS RUN, so it belongs beside every other step
        # that could not get an answer rather than only in the checklist's UNKNOWN cells.
        await ValidationIssueService.record(
            session,
            study,
            [
                {
                    "step": _CAPABILITY_STEPS.get(key, key),
                    "outcome": "unreachable",
                    "impact": "degraded",
                    "message": answer.get("failure_reason") or "",
                    "model": None,
                }
                for key, answer in capabilities.items()
                if isinstance(answer, dict) and answer.get("value") == UNKNOWN
            ],
        )
        return capabilities

    @staticmethod
    async def _run_precompute_checks(
        session: AsyncSession, study: ValidationStudy, plan, *, full_text: str | None, fetcher=None
    ) -> dict:
        """plan_7 step 14: land the four pre-compute checks on ``evidence["precompute_checks"]``.

        Two facts and two judgments. The facts are answered without a model, so a provider outage
        cannot take the species hold with it. Never raises: these inform the gate, they do not gate
        the read.
        """
        from app.services.validation_precompute_checks import run_precompute_checks

        cfg = await llm_provider_config_service.get_for_feature(
            session, study.organization_id, FEATURE_LITERATURE_VALIDATION
        )
        if cfg is None:
            logger.info("study %s: no LLM provider, so the sufficiency judgments are unanswered", study.id)

        evidence = dict(study.evidence_json or {})
        sample_sheet = (plan.sample_sheet_json if plan else None) or {}
        issues: list[dict] = []
        try:
            checks = await run_precompute_checks(
                # The methods section is not separated out of the extraction, so the judgment reads
                # the paper. A model asked "is the methods description detailed enough" over the
                # whole text answers the same question; splitting the paper up here would be our
                # guess at where the section starts.
                methods_text=full_text or "",
                samples_text=full_text or "",
                plan_organism=sample_sheet.get("organism"),
                deposit_organisms=await ValidationDriverService._deposit_organisms(study, fetcher=fetcher),
                paper_sample_count=sample_sheet.get("sample_count"),
                entries=await ValidationDriverService._deposit_entries(study, fetcher=fetcher),
                # Whatever the inventory holds so far. At read time these are named references with
                # no measurements; the assessment stage re-settles the checks once they are resolved.
                supplements=(study.evidence_json or {}).get("supplements") or [],
                # change_7.3 section 8: every archive's listing, not only GEO's. A DOI-requested study
                # handed `list_deposit` an empty accession and the check said "no deposited files were
                # listed" beside an EGA inventory of 108 files.
                deposits=((study.evidence_json or {}).get("capabilities") or {}).get("deposits") or [],
                # A series matrix is the only organism declaration bioAF reads. An EGA deposit has
                # none, and reporting that as "the deposit declares no organism" claimed we had
                # looked at something we never queried.
                organism_source=_has_organism_source(study.evidence_json or {}),
                client=get_client(cfg.provider) if cfg else None,
                model=cfg.model if cfg else "",
                api_key=cfg.api_key if cfg else None,
                on_issue=issues.append,
            )
        except Exception as exc:  # noqa: BLE001 - the checks inform the gate; they cannot fail a read
            logger.warning("pre-compute checks failed for study %s: %s", study.id, exc)
            return {}

        evidence["precompute_checks"] = checks
        study.evidence_json = evidence
        await session.flush()
        await ValidationIssueService.record(session, study, issues)
        return checks

    @staticmethod
    async def _deposit_organisms(study: ValidationStudy, *, fetcher=None) -> list[str]:
        """What the DEPOSIT says its samples are, from the series matrix. Empty when unreachable.

        The depositor's own controlled statement, which is the same authority ``library_strategy``
        takes over a paper's prose when the two disagree.
        """
        from app.services.literature.accession_manifest_service import (
            _http_fetch_text,
            geo_series_matrix_url,
            parse_series_organisms,
        )

        url = geo_series_matrix_url((study.source_accession or "").strip())
        if not url:
            return []
        try:
            return parse_series_organisms(await (fetcher or _http_fetch_text)(url))
        except Exception as exc:  # noqa: BLE001 - an unreachable matrix leaves the check UNKNOWN
            logger.info("study %s: could not read the deposit's declared organism: %s", study.id, exc)
            return []

    @staticmethod
    async def _deposit_entries(study: ValidationStudy, *, fetcher=None) -> list:
        """What the study deposited, for the sample-data check. Empty when unlistable."""
        from app.services.literature.deposit_inventory_service import list_deposit

        inventory = await list_deposit((study.source_accession or "").strip(), fetcher=fetcher)
        return inventory.entries

    # ---- Execution half (background tick) ----

    @staticmethod
    async def advance_active_studies(session: AsyncSession) -> int:
        """Advance every study in an active back-half state by at most one step. Returns the number
        of studies whose state changed. Each study is handled independently and committed on its own,
        so one study's failure (recorded as a retryable ``error``) never blocks the others."""
        ids = list(
            (
                await session.execute(
                    select(ValidationStudy.id).where(
                        or_(
                            ValidationStudy.state.in_(_ACTIVE_BACK_HALF_STATES),
                            # Self-driving front half: the route was chosen at the button, so the read
                            # and the approval are this loop's work rather than two more clicks.
                            and_(
                                ValidationStudy.state.in_(_SELF_DRIVING_FRONT_HALF_STATES),
                                ValidationStudy.intended_route.is_not(None),
                            ),
                        )
                    )
                )
            ).scalars()
        )
        advanced = 0
        for study_id in ids:
            try:
                # change_7.2 section 2: own the study for the length of this step. The id query takes
                # no lock and the loop commits per study, so a lock taken there would be released at
                # the first commit; a claim is what actually keeps two workers off one study.
                async with owned(session, study_id, holder="driver") as claim:
                    if claim is None:
                        continue  # somebody else is working on it; not this tick's business
                    study = (
                        await session.execute(select(ValidationStudy).where(ValidationStudy.id == study_id))
                    ).scalar_one_or_none()
                    if study is None or not _driver_owns(study):
                        continue
                    changed = await ValidationDriverService._advance_one(session, study, claim=claim)
                    await assert_held(session, claim)
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
    async def _advance_one(session: AsyncSession, study: ValidationStudy, *, claim=None) -> bool:
        # change_7.2 section 4: the public assessment runs on EVERY authorized study, on both routes,
        # after authorization and before the final execution-feasibility decision. change_7.1 built
        # supplement resolution, retrieval propagation and reconciliation and attached all of it to
        # the refusal path, so the first study approved by hand walked past every one of them.
        # change_7.2 section 7: stamp the build that is about to do this stage's work. Per stage,
        # because a study can span deployments, and never backwards over stages that ran before this
        # existed.
        record_stage(study, study.state)

        if study.state in _ASSESSMENT_STATES and not (study.evidence_json or {}).get("assessment"):
            await run_assessment(session, study)
            return True

        handlers = {
            "acquiring_data": ValidationDriverService._handle_acquiring_data,
            "acquiring_processed": ValidationDriverService._handle_acquiring_processed,
            "inspecting_deposit": ValidationDriverService._handle_inspecting_deposit,
            "setup": ValidationDriverService._handle_setup,
            "running": ValidationDriverService._handle_running,
            "extracting": ValidationDriverService._handle_extracting,
            "reproducing": ValidationDriverService._handle_reproducing,
            "comparing": ValidationDriverService._handle_comparing,
            "requested": ValidationDriverService._handle_requested,
            "plan_ready": ValidationDriverService._handle_plan_ready,
        }
        handler = handlers.get(study.state)
        if handler is None:
            return False
        # The steps that take this tick's claim: the read races two writers, and the two launches
        # dispatch external work that a database fence cannot recall.
        if study.state in ("requested", "acquiring_data", "setup"):
            return await handler(session, study, claim=claim)
        return await handler(session, study)

    @staticmethod
    async def _handle_requested(session: AsyncSession, study: ValidationStudy, *, claim=None) -> bool:
        """Read the paper without waiting for a "Read paper" click.

        Reached only when the requester chose a route at the button. The click it replaces was the
        first of two hidden stops: the study sat in `requested` rendering "Step 1 of 9" with an
        in-progress badge while nothing at all was happening.
        """
        await ValidationDriverService.read_and_plan(
            session, study, None, study.organization_id, study.requested_by_user_id, claim=claim, holder="driver"
        )
        return True

    @staticmethod
    async def _handle_plan_ready(session: AsyncSession, study: ValidationStudy) -> bool:
        """Approve onto the route chosen at the button, or state why it cannot be taken.

        change_7.2 section 1: the decision comes from the shared policy, so this entrance and the C1
        gate cannot disagree. Study 32 was refused here and reached an outcome; study 33 was approved
        by hand against a byte-identical capability block and ran into an unbounded acquisition loop.
        """
        route = study.intended_route
        if not route:  # pragma: no cover - the loop's predicate already filtered these out
            return False

        evidence = dict(study.evidence_json or {})
        if evidence.get("route_blocked"):
            return False  # already held and explained; re-deciding every 30s would just churn

        decision = await ValidationStudyService.route_decision_for(session, study, route)
        if decision.authorizes_execution:
            try:
                await ValidationStudyService.approve_plan(
                    session,
                    study.id,
                    study.organization_id,
                    study.requested_by_user_id,
                    route=route,
                )
                return True
            except HTTPException as exc:
                # A contested judgment still refuses, and choosing a route upfront does not authorise
                # overriding it. Carry the gate's own words rather than inventing new ones.
                unavailable = str(exc.detail)
        else:
            unavailable = decision.reason

        record_refusal(study, route, decision, unavailable)
        await session.flush()
        logger.info("study %s: chosen route %r cannot be taken (%s): %s", study.id, route, decision.action, unavailable)

        # change_7.1 section 4: a blocked route is not the end of the assessment, and it is not an
        # indefinite hold either. Everything that needs no compute and no credentials still gets done,
        # and then the study reaches a stated outcome.
        await ValidationDriverService._finish_without_execution(session, study, unavailable)
        return True

    @staticmethod
    async def _finish_without_execution(session: AsyncSession, study: ValidationStudy, reason: str) -> None:
        """Assess everything that needs no compute, then state the outcome. Never raises.

        change_7.2 section 4: the assessment itself lives in ``validation_assessment`` so the C1 gate
        can run it too. This is the step that concludes.
        """
        await conclude_without_execution(session, study, reason)

    @staticmethod
    async def _handle_acquiring_data(session: AsyncSession, study: ValidationStudy, *, claim=None) -> bool:
        """Launch fetchngs (first visit), or on its completion run D2 and advance to setup (or, if the
        fetched data is not usable, early-exit to missing_data per spec-02/spec-03)."""
        if _waiting_to_retry(study):
            return False
        if study.data_run_id is None:
            # A scheduled transient-failure retry waits out its backoff before relaunching fetchngs.
            retry_at = (study.evidence_json or {}).get("acquire_retry_at")
            if retry_at and _now() < _parse_iso(retry_at):
                return False
            # change_7.5 section 1.3: reads are fetched only for an analysis that can run, and it can
            # run only on a reference bioAF supplies. Hours of download are not spent first.
            if await ValidationDriverService._refuse_without_reference(session, study):
                return True
            return await ValidationDriverService._launch_fetchngs(session, study, claim=claim)

        run = await ValidationDriverService._load_run(session, study.data_run_id)
        if run is None or run.status in _RUN_FAILED:
            return await ValidationDriverService._handle_acquisition_failure(session, study, run)
        if run.status != _RUN_DONE:
            return False  # still fetching

        # The operation reached its end, so nothing may adopt it any more: a completed run is not a
        # running one, and treating it as adoptable would stop the study relaunching when it should.
        await finish_operation(session, study, _OP_DATA_ACQUISITION)

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
        # change_7.4 section 1.4: only the selected contrast's samples are resolved and required.
        # Every contrast's picks were unioned here, so a sample of a contrast nobody selected could
        # hold a run it has nothing to do with. With no compatible selection there is no differential
        # to resolve, and the QC comparison for scalar claims runs as before.
        selected, _ = selected_contrast_for(
            design,
            pipeline_key=plan.pipeline_key if plan else None,
            library_strategy=plan.library_strategy if plan else None,
        )
        if selected is None:
            return "ok", None
        picked: set[str] = set()
        picked.update(contrasts[selected].get("test_samples") or [])
        picked.update(contrasts[selected].get("reference_samples") or [])
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

        new_contrasts = list(contrasts)
        contrast = contrasts[selected]
        new_subjects: dict[str, str] = {}
        for pick, label in (contrast.get("subjects") or {}).items():
            for external_id in _resolve(pick):
                new_subjects[external_id] = label
        new_contrasts[selected] = {
            **contrast,
            "test_samples": _remap(contrast.get("test_samples")),
            "reference_samples": _remap(contrast.get("reference_samples")),
            "subjects": new_subjects,
        }
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
        session: AsyncSession, study: ValidationStudy, *, fetcher=None, storage_adapter=None, inventory_fetcher=None
    ) -> bool:
        """plan_7 step 5: download the deposited files, decode them, and land them as Files.

        No pipeline run, no Kubernetes, no notebook. This is an HTTP download, which is why the
        deposit route takes minutes where `_handle_acquiring_data` takes hours (36.7 GB / ~5h15m on
        study 22).

        A study still gets an experiment, so a deposit-route study looks like every other one in the
        UI and its files hang off the same place.
        """
        # change_7.2 section 3: a transient failure waits out its backoff. Never a fixed 30-second
        # interval, and never unbounded.
        if _waiting_to_retry(study):
            return False
        evidence = dict(study.evidence_json or {})

        # change_7.4 section 1.5: the deposit route acquires for a differential analysis, and a plan
        # whose contrasts none of which this route can analyze has nothing to acquire for. Study 37
        # downloaded an RNA-seq matrix for a ChIP-seq run whose selector had already said no.
        if not evidence.get("deposit"):
            unanalyzable = await ValidationDriverService._no_compatible_contrast(session, study)
            if unanalyzable:
                return await ValidationDriverService._hold_deposit(
                    session, study, evidence, unanalyzable, cause=NO_COMPATIBLE_CONTRAST
                )

        # The driver ticks repeatedly; re-downloading each time would hammer NCBI and duplicate the
        # File rows.
        if evidence.get("deposit"):
            await ValidationStudyService.transition(
                session, study.id, study.organization_id, study.requested_by_user_id, "inspecting_deposit"
            )
            return True

        # A deposit that holds files but none that can serve is a FINDING, not a wait. Recorded by
        # step 2 (`deposit_blocker`) and surfaced here so the gate can escalate to raw reads, rather
        # than a scientist being told nothing while nine matrices sit in GEO.
        blocker = evidence.get("deposit_unusable")

        selection = evidence.get("deposit_selection") or {}

        # plan_7 step 11: nothing has chosen yet, so choose. Steps 1 and 2 built the inventory and
        # the selection and NOTHING called them, which parked every approved deposit-route study
        # here forever. This is that wire, and it mirrors `_handle_acquiring_data`'s first visit.
        if not selection and not blocker:
            proceed = await ValidationDriverService._choose_from_deposit(
                session, study, evidence, fetcher=inventory_fetcher
            )
            # change_7.4 section 1.1: a hold that is not retried concludes the study where it arose.
            # Falling through would hold the recorded blocker a second time and conclude it twice.
            if study.state != "acquiring_processed":
                return True
            if not proceed:
                return False
            selection = evidence.get("deposit_selection") or {}
            blocker = evidence.get("deposit_unusable")

        wanted = list(selection.get("matrix_files") or [])
        if not wanted:
            if blocker:
                # The cause recorded where the blocker arose. A record written before causes carries
                # only its wording, which is read the old way.
                return await ValidationDriverService._hold_deposit(
                    session, study, evidence, blocker, cause=evidence.get("deposit_unusable_cause")
                )
            # Assisted mode arrives here with nothing chosen yet. A wait, not a failure.
            return False

        from app.services import deposit_acquisition
        from app.services.file_service import FileService
        from app.services.literature.deposit_inventory_service import series_suppl_url

        listed = (evidence.get("deposit_inventory") or {}).get("accession") or study.source_accession or ""
        base = series_suppl_url(listed)
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
                # change_7.4 section 1.1: the only hold here that reads an error, and it reads the
                # retrieval call's own: a 404 names its location, a 403 is a refusal, not an access model.
                return await ValidationDriverService._hold_deposit(
                    session,
                    study,
                    evidence,
                    f"{filename} could not be downloaded from GEO ({exc})",
                    cause=retrieval_cause(exc),
                    resource=filename,
                    location=url,
                )
            try:
                text, fmt = deposit_acquisition.decode_deposit(filename, raw)
            except deposit_acquisition.UnreadableDepositError as exc:
                return await ValidationDriverService._hold_deposit(
                    session, study, evidence, str(exc), cause=INPUT_UNREADABLE, resource=filename
                )
            except deposit_acquisition.DepositTooLargeError as exc:
                return await ValidationDriverService._hold_deposit(
                    session, study, evidence, str(exc), cause=RESOURCE_LIMIT, resource=filename
                )

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
                    # Where the DECODED copy landed. `_handle_inspecting_deposit` reads the matrix
                    # back from here, and without it every deposit held on "could not be read back"
                    # the moment it reached inspection. Found by step 12's end-to-end walk; every
                    # per-step test had planted the record by hand with this key already on it.
                    "storage_uri": uri,
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

        if _waiting_to_retry(study):
            return False
        evidence = dict(study.evidence_json or {})
        deposit = evidence.get("deposit") or {}
        matrices = [f for f in deposit.get("files") or [] if f.get("artifact_type") == "deposited_matrix"]
        if not matrices:
            return await ValidationDriverService._reacquire_incomplete_deposit(session, study, evidence)

        storage = storage_adapter or get_storage_adapter()
        try:
            text = await storage.read_text(matrices[0]["storage_uri"])
        except Exception as exc:  # noqa: BLE001
            return await ValidationDriverService._hold_deposit(
                session,
                study,
                evidence,
                f"the acquired deposit could not be read back: {exc}",
                cause=RETRIEVAL_TRANSIENT,
                resource=f"the stored copy of {matrices[0].get('filename') or 'the acquired matrix'}",
            )

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        design = (plan.differential_design_json if plan else None) or {}
        # change_7.5 section 1.7 (finishing change_7.4 section 1.4): the selected contrast's samples
        # only. The others are not what this matrix is being read for.
        design_samples: list[str] = []
        selected_index, _ = (
            selected_contrast_for(design, pipeline_key=plan.pipeline_key, library_strategy=plan.library_strategy)
            if plan is not None and design.get("contrasts")
            else (None, None)
        )
        if selected_index is not None:
            chosen = design["contrasts"][selected_index]
            design_samples.extend(chosen.get("test_samples") or [])
            design_samples.extend(chosen.get("reference_samples") or [])

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
            return await ValidationDriverService._hold_deposit(
                session,
                study,
                evidence,
                f"{matrices[0].get('filename') or 'the deposited matrix'}: "
                f"{inspection['unusable_reason'] or 'the deposited matrix is not usable'}",
                cause=inspection.get("unusable_cause") or INPUT_UNREADABLE,
                resource=matrices[0].get("filename"),
            )

        # plan_7 step 7: work out what each COLUMN is, then rewrite the design onto those columns so
        # the differential test matches its input by construction. Mirrors `_resolve_sample_design`
        # on the pipeline route, including its held-before-compute contract.
        from app.services.deposit_metadata_association import (
            associate_columns,
            empty_arm_cause,
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
            # change_7.4 section 1.4: only the selected contrast is validated and rewritten.
            selected, _ = selected_contrast_for(
                design, pipeline_key=plan.pipeline_key, library_strategy=plan.library_strategy
            )
            rewritten, status, reason = rewrite_design_to_columns(design, associations, contrast_index=selected)
            if status in ("mismatch", "pairing_lost"):
                # change_7.4 sections 1.1 and 1.4: never retried. Whether the input lacks the
                # condition or the columns could not be placed is decided by what placed them, and a
                # pairing that cannot be carried is an unresolved mapping, never an unpaired run.
                return await ValidationDriverService._hold_deposit(
                    session,
                    study,
                    evidence,
                    f"bioAF downloaded and read {matrices[0].get('filename') or 'the deposited matrix'}. "
                    f"{reason or 'design mismatch'}",
                    cause=empty_arm_cause(associations) if status == "mismatch" else SAMPLE_MAPPING_UNRESOLVED,
                    resource=matrices[0].get("filename"),
                )
            plan.differential_design_json = rewritten
            await session.flush()

        # Build the SAME level3 bundle the pipeline route builds in `_handle_extracting`, out of the
        # deposit instead of a run. This is where the two routes converge: `_handle_reproducing`
        # reads evidence["level3"] and is deliberately untouched by plan_7.
        decision = await resolve_level3_from_deposit(session, study, plan, evidence=evidence)
        if decision.inputs:
            evidence["level3"] = decision.inputs
            _record_readiness(evidence, "yes", _readiness_statement(decision.inputs))
        else:
            evidence["level3_skipped"] = {"reason": decision.reason, "reason_code": decision.reason_code}
            _record_readiness(evidence, "no", decision.reason, cause=decision.reason_code)

        evidence.pop("deposit_failed", None)
        study.evidence_json = evidence
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "reproducing"
        )
        return True

    @staticmethod
    async def _choose_from_deposit(
        session: AsyncSession, study: ValidationStudy, evidence: dict, *, fetcher=None
    ) -> bool:
        """plan_7 step 11: list what the study deposited and decide what to reproduce from.

        Returns True when a selection now exists and the caller should fall through to the download,
        False when the study holds (nothing listable, nothing usable, or a person's turn).

        **In the driver, not in the approve endpoint.** Post-approval work belongs here: a GEO blip
        must not fail an approval, this retries for free on the next tick, and the model is paid only
        when the route is actually taken.
        """
        from dataclasses import asdict

        from app.services.archive_discovery import GEO, can_acquire_from
        from app.services.deposit_selection import select_deposit, unusable_listing
        from app.services.literature.deposit_inventory_service import list_deposit

        # change_7.2 section 3: resolve the accession the way discovery already does, and dispatch by
        # ARCHIVE. This read `study.source_accession` alone, which is NULL on a study requested by
        # DOI, so `list_deposit` received an empty string and answered "the accession is not a GEO
        # series id" -- the `or 'the accession'` fallback rather than a statement about EGA. The
        # requested accession keeps its authority over what a run actually fetches.
        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        target = acquisition_accession(_named_accessions(study, plan), prefer_archive=GEO)
        if target is None:
            # change_7.4 section 1.1: the reading named nothing bioAF can point at. That is not a
            # statement by the paper that nothing was deposited, so it is never an absence.
            return await ValidationDriverService._hold_deposit(
                session,
                study,
                evidence,
                "this paper names no deposit accession that an acquisition could be pointed at",
                cause=INPUT_UNIDENTIFIED,
            )
        accession = target["accession"]
        archive = target["archive"]
        if not can_acquire_from(archive):
            # An EGA study must be refused because bioAF has no adapter for EGA, not because an empty
            # string failed a GEO pattern test.
            return await ValidationDriverService._hold_deposit(
                session,
                study,
                evidence,
                f"{accession} is deposited in {archive.upper()}",
                cause=NO_ADAPTER,
                archive=archive,
                resource=accession,
            )
        inventory = await list_deposit(accession, fetcher=fetcher)
        if inventory.unavailable_reason:
            return await ValidationDriverService._hold_deposit(
                session,
                study,
                evidence,
                inventory.unavailable_reason,
                cause=inventory.unavailable_cause,
                archive=archive,
                resource=f"GEO's supplementary listing for {accession}",
                location=inventory.listing_url,
            )

        entries = inventory.entries
        # change_7.5 section 1.7: the repository's own sample records, written where the deposit is
        # listed. Column association has read `evidence["sample_manifest"]` since plan_7 step 7 and
        # nothing ever wrote it, so GEO's sample titles and GSMs reached it only in a unit test.
        await ValidationDriverService._record_sample_manifest(evidence, accession, fetcher=fetcher)
        # `unusable_listing` names what IS deposited and why it cannot serve (GSE312719's nine
        # pre-cell-calling matrices), and says when the classifier could not place the files at all.
        unusable = unusable_listing(entries, accession)
        if unusable:
            cause, reason = unusable
            evidence["deposit_unusable"] = reason
            evidence["deposit_unusable_cause"] = cause
            return await ValidationDriverService._hold_deposit(
                session, study, evidence, reason, cause=cause, resource=accession
            )

        # The inventory is kept whichever way the choice is made: in `assisted` it is the list the
        # C1 gate shows a person, and in `autonomous` it is what the model chose FROM, which is part
        # of the record (a choice among three files reads differently from one among thirty).
        evidence["deposit_inventory"] = {
            "accession": accession,
            "source": inventory.source,
            "listed_at": _now().isoformat(),
            "entries": [asdict(e) for e in entries],
            "triplets": inventory.triplets,
        }
        # A COPY on every assignment. The caller keeps mutating `evidence` and assigns it again on
        # its way out, and re-assigning the identical object leaves the column looking unchanged, so
        # the download's own `evidence["deposit"]` never reached the database. Found by step 12
        # driving the whole route: the inventory persisted and everything after it silently did not.
        study.evidence_json = dict(evidence)

        org = await session.get(Organization, study.organization_id)
        autonomy = (org.lit_validation_autonomy if org else None) or AUTONOMY_ASSISTED
        if autonomy != AUTONOMY_AUTONOMOUS:
            return False  # a person picks at the C1 gate

        cfg = await llm_provider_config_service.get_for_feature(
            session, study.organization_id, FEATURE_LITERATURE_VALIDATION
        )
        if cfg is None:
            logger.warning("study %s is autonomous but the org has no LLM provider; the deposit pick waits", study.id)
            return False

        claim = (plan.finding_claim_json if plan else None) or {}
        issues: list[dict] = []
        chosen = await select_deposit(
            entries,
            pipeline_key=plan.pipeline_key if plan else None,
            kind=claim.get("kind"),
            client=get_client(cfg.provider),
            model=cfg.model,
            api_key=cfg.api_key,
            on_issue=issues.append,
        )
        await ValidationIssueService.record(session, study, issues)
        if chosen is None:
            # The ask failed. Same direction the ratifier takes: hold where the assisted policy
            # holds, so the gate can pick rather than a provider outage choosing the file.
            return False

        evidence["deposit_selection"] = chosen
        if chosen.get("declined"):
            # Looked and said no. A finding, and not the same as never having looked; and a decision
            # from filenames, so never an established absence (change_7.4 section 1.1).
            declined = chosen.get("reason") or "the model found nothing in this deposit worth reproducing from"
            evidence["deposit_unusable"] = declined
            evidence["deposit_unusable_cause"] = INPUT_UNIDENTIFIED
            return await ValidationDriverService._hold_deposit(
                session, study, evidence, declined, cause=INPUT_UNIDENTIFIED, resource=accession
            )

        study.evidence_json = dict(evidence)
        return True

    @staticmethod
    async def _record_sample_manifest(evidence: dict, accession: str, *, fetcher=None) -> None:
        """Land the deposit's per-sample records on ``evidence["sample_manifest"]``. Never raises.

        An unreachable manifest leaves nothing written and says why, so association falls back to the
        sources below it exactly as it did before.
        """
        from app.services.literature.accession_manifest_service import AccessionManifestService

        try:
            manifest = await AccessionManifestService.fetch_manifest(accession, fetcher=fetcher)
        except Exception as exc:  # noqa: BLE001 - the service never raises; association must not fail on it
            logger.info("sample manifest for %s could not be read: %s", accession, exc)
            return
        if manifest.samples:
            evidence["sample_manifest"] = manifest.samples
        elif manifest.unavailable_reason:
            evidence["sample_manifest_unavailable"] = manifest.unavailable_reason

    @staticmethod
    async def _no_compatible_contrast(session: AsyncSession, study: ValidationStudy) -> str | None:
        """Why no contrast of the plan can be analyzed on this route, or None when one can (or the
        plan declares none, which is a QC-only paper and not this refusal)."""
        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        design = (plan.differential_design_json if plan else None) or {}
        if not design.get("contrasts"):
            return None
        selected, why = selected_contrast_for(
            design, pipeline_key=plan.pipeline_key, library_strategy=plan.library_strategy
        )
        if selected is not None:
            return None
        return f"No contrast of this paper can be analyzed on this route: {why}."

    @staticmethod
    async def _reacquire_incomplete_deposit(session: AsyncSession, study: ValidationStudy, evidence: dict) -> bool:
        """An acquisition record with no matrix in it: bioAF's own inconsistency, not the deposit's.

        change_7.4 section 1.1: this was held as "no deposited matrix was acquired to inspect", which
        read as a finding about the deposit. It is recorded as an issue, the incomplete record is
        cleared, and acquisition runs again under the same attempt bound.
        """
        await ValidationIssueService.record(
            session,
            study,
            [
                {
                    "step": "inspecting the deposited matrix",
                    "outcome": "internal",
                    "impact": "degraded",
                    "message": "bioAF's record of the acquired deposit held no matrix, so the deposit is being acquired again.",
                    "model": None,
                }
            ],
        )
        evidence.pop("deposit", None)
        attempts = int(evidence.get("acquisition_attempts") or 0) + 1
        evidence["acquisition_attempts"] = attempts
        if exhausted(attempts):
            evidence.pop("acquisition_retry_at", None)
            study.evidence_json = dict(evidence)
            await conclude_without_execution(
                session,
                study,
                "bioAF's record of the acquired deposit was incomplete",
                limitation={
                    "kind": "failed_discovery",
                    "resource": (evidence.get("deposit_inventory") or {}).get("accession")
                    or study.source_accession
                    or "this paper's deposits",
                    "operation": study.intended_route or "deposit",
                    "detail": (
                        f"bioAF's record of the acquired deposit was incomplete after {attempts} attempts, so the "
                        "input it would analyze was never established"
                    ),
                },
            )
            return True
        evidence["acquisition_retry_at"] = (_now() + timedelta(seconds=backoff_for(attempts))).isoformat()
        study.evidence_json = dict(evidence)
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "acquiring_processed"
        )
        return True

    @staticmethod
    async def _hold_deposit(
        session: AsyncSession,
        study: ValidationStudy,
        evidence: dict,
        reason: str,
        *,
        cause: str | None = None,
        archive: str | None = None,
        resource: str | None = None,
        location: str | None = None,
    ) -> bool:
        """Act on why acquisition could not proceed.

        change_7.2 section 3: this used to produce one behaviour for three situations. It recorded a
        reason, logged it, and returned False with no transition and no backoff, so study 33 repeated
        the same failing listing every 30 seconds until a person stopped it with a database write and
        study 29 has been in that loop since 2026-09-07.

        change_7.4 section 1.1: the caller passes ``cause``, because the caller knows what failed.
        Study 37's design rewrite was read from its wording, matched nothing, and was retried and
        reported as "could not reach the deposit". A hold raised with no cause is retrieval text and
        is read the old way, where "unrecognised means transient" is still the right rule.
        ``resource`` and ``location`` are what an exhausted retrieval names.

        Not `error` in any case: a legacy .xls or a withdrawn supplementary file is a fact about the
        deposit, not an infrastructure failure.
        """
        outcome = outcome_for(cause, reason, archive=archive) if cause else classify_hold(reason, archive=archive)
        evidence["deposit_failed"] = {
            "reason": outcome.reason,
            "kind": outcome.kind,
            "action": outcome.action,
            "cause": outcome.cause,
            "at": _now().isoformat(),
        }
        resource = resource or (evidence.get("deposit_inventory") or {}).get("accession") or study.source_accession

        if outcome.kind == AWAITING_INPUT:
            # A person's turn. Visible, and logged ONCE rather than on every tick.
            if not evidence.get("awaiting_choice"):
                logger.info("validation study %d: waiting for a person to choose: %s", study.id, outcome.reason)
            evidence["awaiting_choice"] = {
                "reason": outcome.reason,
                "at": _now().isoformat(),
                "action": "choose a deposited file at the gate, or cancel the acquisition",
            }
            study.evidence_json = dict(evidence)
            return False

        if outcome.is_transient:
            attempts = int(evidence.get("acquisition_attempts") or 0) + 1
            evidence["acquisition_attempts"] = attempts
            if not exhausted(attempts):
                wait = backoff_for(attempts)
                evidence["acquisition_retry_at"] = (_now() + timedelta(seconds=wait)).isoformat()
                study.evidence_json = dict(evidence)
                logger.info(
                    "validation study %d: acquisition failed transiently (attempt %d of %d), retrying in %ds: %s",
                    study.id,
                    attempts,
                    MAX_ATTEMPTS,
                    wait,
                    outcome.reason,
                )
                return False
            # Exhausted. Nothing was established, so the evidence conclusion stays UNDETERMINED and
            # the workflow closes: an exhausted discovery must never harden into an inferred absence,
            # and it must not stay open either.
            evidence.pop("acquisition_retry_at", None)
            evidence["discovery_unresolved"] = {
                "reason": outcome.reason,
                "attempts": attempts,
                "at": _now().isoformat(),
                "resume": "a further attempt needs explicit resumption or new evidence",
            }
            study.evidence_json = dict(evidence)
            logger.info("validation study %d: acquisition attempts exhausted: %s", study.id, outcome.reason)
            await conclude_without_execution(
                session,
                study,
                outcome.reason,
                limitation={
                    "kind": outcome.limitation_kind,
                    "resource": resource or "this paper's deposits",
                    "operation": study.intended_route or "deposit",
                    # change_7.4 section 1.1: built from the cause. "Could not reach" is written for
                    # a failure to reach, and a 404 names where it was looked for.
                    "detail": exhaustion_detail(
                        outcome.cause,
                        attempts=attempts,
                        resource=resource or "the deposit",
                        reason=outcome.reason,
                        location=location,
                    ),
                    "technical_detail": _technical_detail(outcome.cause, location, attempts=attempts),
                },
            )
            return True

        # Terminal, and it says which one it is.
        if outcome.cause in READINESS_CAUSES:
            # change_7.4 section 1.3: the input was acquired and read; what failed is whether the
            # selected analysis can run on it, and that is its own recorded fact.
            _record_readiness(evidence, "no", outcome.reason, cause=outcome.cause)
        study.evidence_json = dict(evidence)
        logger.info(
            "validation study %d: acquisition refused (%s): %s",
            study.id,
            outcome.cause or outcome.action,
            outcome.reason,
        )
        await conclude_without_execution(
            session,
            study,
            outcome.reason,
            limitation={
                "kind": outcome.limitation_kind,
                "resource": resource or "this paper's deposits",
                "operation": study.intended_route or "deposit",
                "detail": outcome.reason,
                "technical_detail": _technical_detail(outcome.cause, location),
            },
        )
        return True

    @staticmethod
    async def _handle_setup(session: AsyncSession, study: ValidationStudy, *, claim=None) -> bool:
        """Launch the analysis pipeline (D3) against the set-up experiment and advance to running."""
        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        if plan is None or not plan.pipeline_key:
            return await ValidationDriverService._fail(session, study, "no pipeline in the approved plan")
        # change_7.5 section 1.3: no validation launch runs on a pipeline's seeded genome.
        if await ValidationDriverService._refuse_without_reference(session, study, plan=plan):
            return True

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
        # The same guarantee the data acquisition has: one identity per logical attempt, written
        # before dispatch, and a run a previous worker started adopted rather than duplicated.
        adopted = await ValidationDriverService._adopt_running_operation(session, study, _OP_ANALYSIS)
        if adopted is not None:
            return adopted

        await begin_operation(session, study, _OP_ANALYSIS, claim=claim, kind=plan.pipeline_key)
        await assert_held(session, claim)
        run = await ValidationDriverService._launch(session, study, launch)
        study.analysis_run_id = run.id
        await record_dispatch(session, study, _OP_ANALYSIS, external_id=run.id, claim=claim)
        if run.status in _RUN_FAILED:
            return await ValidationDriverService._fail(session, study, "analysis run failed to launch")

        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "running"
        )
        return True

    @staticmethod
    async def _refuse_without_reference(session: AsyncSession, study: ValidationStudy, *, plan=None) -> bool:
        """Conclude a raw-reads study whose reference bioAF cannot supply. False when it can.

        change_7.5 section 1.3: with no genome, a launch kept the pipeline's seeded parameters, and
        nf-core/rnaseq is seeded with GRCh38, so a mouse paper would have aligned to human. Reanalysis
        from raw reads, and the QC metrics a run computes, depend on the reference; nothing on this
        route runs without one, and nothing substitutes a default or another assembly.
        """
        from app.services.validation_assessment import conclude_without_execution
        from app.services.validation_reference import USABLE, paper_reference, reference_limitation

        if plan is None:
            plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        # A plan written before `reference_build` existed carries only the launch token.
        stated = (plan.reference_build or plan.reference_genome) if plan else None
        reference = paper_reference(stated, plan.pipeline_key if plan else None)
        if reference["status"] == USABLE:
            return False
        limitation = reference_limitation(reference, operation=study.intended_route or "pipeline")
        logger.info("validation study %d: refused a reference-dependent run: %s", study.id, limitation["detail"])
        await conclude_without_execution(session, study, limitation["detail"], limitation=limitation)
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
        # change_7.2 sections 5 and 6: the comparison branches on the binding decision, the basis and
        # the claim's own wording, and none of them reached it. Five fields were serialized here and
        # every column migrations 134 and 135 added was dropped on the way, so a recorded binding was
        # invisible to the code whose whole job is to use it.
        targets = [
            {
                "metric_key": t.metric_key,
                "claim_text": t.claim_text,
                "claimed_value": t.claimed_value,
                "unit": t.unit,
                "tolerance": t.tolerance,
                "source_locator": t.source_locator,
                "sample_subset": t.sample_subset,
                "qc_stage": t.qc_stage,
                "direction": t.direction,
                "threshold": t.threshold,
                "threshold_kind": t.threshold_kind,
                "output_type": t.output_type,
                "measurement_basis": t.measurement_basis,
                "bound_key": t.bound_key,
                "binding_reason": t.binding_reason,
                "binding_confidence": t.binding_confidence,
                "bound_by_model": t.bound_by_model,
                "bound_by": t.bound_by,
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
                _record_readiness(evidence, "yes", _readiness_statement(decision.inputs))
            elif decision.reason_code not in _LEVEL3_NEVER_CONFIGURED:
                # The human confirmed a ground-truth set and something else stopped the finding step.
                # Record which, so an `inconclusive` can say a configured Level-3 did not run instead
                # of leaving the only account of it in a server log.
                evidence["level3_skipped"] = {"reason": decision.reason, "reason_code": decision.reason_code}
                _record_readiness(evidence, "no", decision.reason, cause=decision.reason_code)

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
        # plan_7 step 18: a study bioAF could not wire a template for is exactly the study the
        # generated arm exists for, so the early return to `comparing` cannot come before the
        # ladder. Study 26 is the shape: a confirmed finding, no route from bioAF's own wiring to
        # it, and nothing produced at all.
        if not level3 and not evidence.get("level3_skipped") and not evidence.get("code_execution"):
            await ValidationStudyService.transition(
                session, study.id, study.organization_id, study.requested_by_user_id, "comparing"
            )
            return True

        # plan_7 step 17: the method ladder. Evaluated ONCE, on the first visit, and once an arm is
        # attempted its result is the result: an attempted published-code execution that fails is
        # preserved as that arm's outcome, and no generated or template run starts behind it.
        # Silently replacing `dependency_unresolvable` with a run that agrees would turn the single
        # most useful finding this feature can produce into a false reproduction.
        # plan_7 step 16, wired: fetch and pin the authors' code on the FIRST visit. Post-approval
        # network work belongs to the driver, for the reason step 11 gives: a GitHub blip must not
        # fail an approval, and this retries for free on the next tick. A service nothing calls is
        # not a feature, which is the lesson step 11 exists to record.
        if "code_resolution" not in evidence:
            await ValidationDriverService._resolve_authors_code(session, study, evidence)
            level3 = evidence.get("level3")

        record = evidence.get("code_execution") or {}
        if (record and not record.get("outcome")) or (
            not record and await ValidationDriverService._wants_code_arm(session, evidence)
        ):
            return await ValidationDriverService._handle_code_arm(session, study, evidence, level3 or {})

        # An arm that was ATTEMPTED owns the result. `attempt: 0` means no arm ran at all (this
        # install cannot execute fetched code), which is the one case that still falls through to
        # bioAF's own template; anything else goes to comparison carrying what it found. Re-entered
        # here only after a restart, since the landing path transitions on its own.
        if record.get("outcome") and record.get("attempt", 0) >= 1:
            await ValidationStudyService.transition(
                session, study.id, study.organization_id, study.requested_by_user_id, "comparing"
            )
            return True

        # plan_7 step 18, rung 2 of the owner's ladder: no usable published code was AVAILABLE, so
        # write the analysis from what the paper describes and run that. A wired template does not
        # pre-empt it (see `_wants_generated_arm`). It does NOT run behind an attempted code arm that
        # failed: that failure is the result of its own arm, and replacing it with a generated run
        # that agrees would turn the most useful finding this feature can produce into a false
        # reproduction. `record` being present at this point means an arm already finished.
        if not record and await ValidationDriverService._wants_generated_arm(session, evidence):
            return await ValidationDriverService._handle_generated_arm(session, study, evidence, level3 or {})

        # Published code we resolved and pinned, on an install that cannot run it. A true statement
        # about this bioAF rather than about the paper, and the report has to be able to say it, so
        # it is recorded before the template arm takes over.
        await ValidationDriverService._record_code_arm_unavailable(session, study, evidence)

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

        from app.services.result_set_normalizer import missing_measure
        from app.services.validation_claim_cutoffs import normalizer_arguments

        # change_7.4 section 1.6: the bundle carries the cutoffs it was built with; one that carries
        # none is never scored at a default. change_7.5 section 1.2: with their kind and operators, so
        # a claim stated at P < 0.01 is filtered at P < 0.01. A bundle written before that carries the
        # legacy pair, read the way it was always applied.
        applied = normalizer_arguments(level3.get("cutoffs"), legacy=params)
        if applied is None:
            return await ValidationDriverService._degrade_to_level2(
                session, study, evidence, "the reproduction's statistical cutoff was not recorded with it"
            )
        our_fs = await ValidationDriverService._extract_reproduced_set(
            session, cs, level3.get("kind", "gene"), **applied
        )
        # A table the stated definition cannot be applied to is not an empty result: scoring it would
        # report the paper's whole set as missed.
        missing = missing_measure(our_fs)
        if missing:
            return await ValidationDriverService._degrade_to_level2(
                session,
                study,
                evidence,
                f"the reproduction's result table could not be read at the stated cutoff: {missing}",
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
    async def _wants_code_arm(session: AsyncSession, evidence: dict) -> bool:
        """Rungs 1 and 2 of the ladder: is there published code we resolved and pinned, and can this
        install actually run it?

        An install with no isolated identity (step 16a) falls to bioAF's own template rather than
        holding: the study still gets a reproduction, and the report says the authors' code could
        not be run here. Borrowing the notebook runner's credential is never the fallback.
        """
        from app.services.untrusted_execution import untrusted_identity

        resolution = evidence.get("code_resolution") or {}
        if resolution.get("outcome") != "resolved":
            return False
        return await untrusted_identity(session) is not None

    @staticmethod
    async def _wants_generated_arm(session: AsyncSession, evidence: dict) -> bool:
        """Rung 2 of the owner's ladder: the paper published no usable code, so reverse-engineer its
        analysis from the methods section. Requires an install that can execute one.

        **The ladder has three rungs and no template rung** (owner, 2026-09-07, verbatim): "If code
        exists, we use the code. If no code exists, we attempt to reverse engineer from the methods
        section. If the methods section has no computational methods or not enough for us to come up
        with anything, we stop and report this."

        So a wired `level3` template is NOT a reason to skip this arm. bioAF's four notebook
        templates are not a third reproduction METHOD ranked between the authors' code and a
        generated analysis: ``template_for_value_type`` picks one from the measured SHAPE of the data
        (counts vs normalized, gene vs interval) and nothing from the paper's methods reaches that
        choice. Running our generic two-arm DESeq2 or limma-trend test on their data is a different
        claim from reproducing their analysis, and only the second is what this feature promises.

        The third rung is decided by the ATTEMPT, not by a veto: step 18 generates once, and where
        the description does not support a runnable analysis it lands ``generation_failed`` with a
        named reason and reaches a terminal state. A thin methods section is therefore not part of
        this test either. Step 14's sufficiency judgment is advisory and is carried as a qualifier,
        which is what plan_7 amendment 2 requires.
        """
        from app.services.untrusted_execution import untrusted_identity

        resolution = evidence.get("code_resolution") or {}
        if resolution.get("outcome") not in ("code_absent", "code_unreachable"):
            return False
        return await untrusted_identity(session) is not None

    @staticmethod
    async def _handle_generated_arm(
        session: AsyncSession, study: ValidationStudy, evidence: dict, level3: dict
    ) -> bool:
        """plan_7 step 18: generate the paper's analysis from its prose, ONCE, and run it.

        Two terminal shapes and neither waits: an executable analysis that goes down the same
        execution path the authors' code uses, or ``generation_failed`` with a named limitation.
        There is no loop through fresh generations and no hold for an unspecified intervention:
        repeating measures the GENERATOR rather than the paper.
        """
        from app.services.code_execution_service import GENERATION_FAILED, build_observation
        from app.services.generated_analysis import generate_analysis
        from app.services.untrusted_execution import untrusted_identity
        from app.services.validation_precompute_checks import CHECK_METHODS, MISMATCH

        cfg = await llm_provider_config_service.get_for_feature(
            session, study.organization_id, FEATURE_LITERATURE_VALIDATION
        )
        if cfg is None:
            return False  # nothing to generate with; the template arm takes over on this tick

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        design = ((plan.differential_design_json if plan else None) or {}).get("contrasts") or [{}]
        inspection = evidence.get("deposit_inspection") or {}
        parameters = level3.get("parameters") or {}
        checks = evidence.get("precompute_checks") or {}
        methods_inadequate = (checks.get(CHECK_METHODS) or {}).get("verdict") == MISMATCH

        generated = await generate_analysis(
            methods_text=str((plan.mapping_notes if plan else "") or "") or evidence.get("paper_text", ""),
            matrix_description=(
                f"{inspection.get('n_rows', 'an unknown number of')} rows and "
                f"{inspection.get('n_columns', 'an unknown number of')} sample columns "
                f"({', '.join(inspection.get('columns') or [])}) at {parameters.get('counts_path', '/data')}"
            ),
            design=design[0],
            methods_inadequate=methods_inadequate,
            client=get_client(cfg.provider),
            model=cfg.model,
            api_key=cfg.api_key,
        )

        if generated["outcome"] == GENERATION_FAILED:
            return await ValidationDriverService._land_code_outcome(
                session,
                study,
                evidence,
                record={
                    "attempt": 1,
                    "method": generated["method"],
                    "qualifiers": generated["qualifiers"],
                    "source": {"generated_by_model": generated["model"], "assumptions": []},
                },
                observation=build_observation(
                    outcome=GENERATION_FAILED,
                    exit_code=None,
                    transcript_uri=None,
                    transcript_tail="",
                    qualifiers=generated["qualifiers"],
                ),
                reason=generated["reason"],
            )

        identity = await untrusted_identity(session)
        storage = get_storage_adapter()
        uri = storage.build_uri(identity.bucket, f"{identity.prefix_for(study.id)}/{generated['entry_point']}")
        try:
            await storage.write_text(uri, generated["source"], content_type="text/plain")
        except Exception as exc:  # noqa: BLE001 - a staging failure is an outcome, not a crash
            return await ValidationDriverService._land_code_outcome(
                session,
                study,
                evidence,
                record={
                    "attempt": 1,
                    "method": generated["method"],
                    "qualifiers": generated["qualifiers"],
                    "source": {},
                },
                observation=build_observation(
                    outcome=GENERATION_FAILED, exit_code=None, transcript_uri=None, transcript_tail=str(exc)
                ),
                reason=f"the generated analysis could not be staged for execution ({exc})",
            )

        cs = await NotebookExecutionService.execute_fetched_code(
            session,
            org_id=study.organization_id,
            user_id=study.requested_by_user_id,
            code_uri=uri,
            entry_point=generated["entry_point"],
            arguments="",
            input_file_ids=level3.get("input_file_ids") or [],
            experiment_id=study.experiment_id,
        )

        # The generated source itself, kept whole. A reader disputing this result has to be able to
        # read exactly what ran, and "an analysis a model wrote" is not readable without it.
        evidence["generated_analysis"] = generated
        evidence["code_execution"] = {
            "attempt": 1,
            "method": generated["method"],
            "qualifiers": generated["qualifiers"],
            "source": {
                "generated_by_model": generated["model"],
                "generated_source_uri": uri,
                "assumptions": generated["assumptions"],
            },
            "session_id": cs.id,
            "entry_point": generated["entry_point"],
        }
        study.evidence_json = dict(evidence)
        await session.flush()
        return True

    @staticmethod
    async def _resolve_authors_code(
        session: AsyncSession, study: ValidationStudy, evidence: dict, *, fetcher=None, storage_adapter=None
    ) -> None:
        """plan_7 step 16: fetch, pin and stage the authors' code. Never raises.

        Also answers ACCESSIBILITY, which step 13 deliberately left open: existence was established
        at read time, and only an attempted fetch can settle whether a source can be reached. The
        answer is written back onto each capability source so the checklist can say "GitHub, exists,
        not accessible: repository is private" rather than flattening the two facts into one cell.
        """
        from app.services.code_fetch_service import RESOLVED, resolve_code

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        sources = (plan.code_availability_json if plan else None) or []
        inventory = (evidence.get("deposit_inventory") or {}).get("entries") or []
        entries = [
            SimpleNamespace(
                filename=e.get("filename"),
                url=e.get("url"),
                classification=e.get("classification"),
                level=e.get("level"),
            )
            for e in inventory
        ]

        resolution = await resolve_code(
            sources=sources, deposit_entries=entries, fetcher=fetcher or _deposit_bytes_fetcher
        )
        record = {
            "outcome": resolution.outcome,
            "kind": resolution.kind,
            "url": resolution.url,
            "commit_sha": resolution.commit_sha,
            "files": resolution.files,
            "reason": resolution.reason,
            "attempts": resolution.attempts,
        }

        # Step 13 recorded `accessible: not_attempted`. This is the attempt.
        capabilities = dict(evidence.get("capabilities") or {})
        if capabilities.get("code_sources"):
            capabilities["code_sources"] = [
                {**src, **_accessibility_update(resolution.accessibility.get(src.get("url") or ""))}
                for src in capabilities["code_sources"]
            ]
            evidence["capabilities"] = capabilities

        if resolution.outcome == RESOLVED and resolution.archive:
            uri = await ValidationDriverService._stage_untrusted_code(
                session, study, resolution, storage_adapter=storage_adapter
            )
            if uri:
                record["archive_uri"] = uri
            entry = await ValidationDriverService._choose_code_entry_point(session, study, resolution)
            if entry:
                evidence["code_entry_point"] = entry

        evidence["code_resolution"] = record
        study.evidence_json = dict(evidence)
        await session.flush()

    @staticmethod
    async def _stage_untrusted_code(
        session: AsyncSession, study: ValidationStudy, resolution, *, storage_adapter=None
    ) -> str | None:
        """Copy the fetched archive into the ONE bucket untrusted code can reach (step 16a).

        None when this install has no such bucket, which is the same condition that stops the arm
        running at all; the caller records why and falls to bioAF's own template.
        """
        from app.services.untrusted_execution import untrusted_identity

        identity = await untrusted_identity(session)
        if identity is None:
            return None
        storage = storage_adapter or get_storage_adapter()
        uri = storage.build_uri(identity.bucket, f"{identity.prefix_for(study.id)}/code.tar.gz")
        try:
            await storage.write_bytes(uri, resolution.archive, content_type="application/gzip")
        except Exception as exc:  # noqa: BLE001 - a staging failure is an outcome, not a crash
            logger.warning("validation study %s: could not stage the fetched code: %s", study.id, exc)
            return None
        return uri

    @staticmethod
    async def _choose_code_entry_point(session: AsyncSession, study: ValidationStudy, resolution) -> dict | None:
        """Which script starts the analysis. A stated choice, a reason, a confidence.

        None when the org has no provider: the driver then falls back to the lone-script rule, which
        needs no model at all, and reports honestly when there is no single obvious answer.
        """
        from app.services.code_execution_service import choose_entry_point

        cfg = await llm_provider_config_service.get_for_feature(
            session, study.organization_id, FEATURE_LITERATURE_VALIDATION
        )
        if cfg is None:
            return None
        readme = next((f for f in resolution.files if str(f.get("path", "")).lower().startswith("readme")), None)
        return await choose_entry_point(
            files=resolution.files,
            readme=str(readme.get("path", "")) if readme else "",
            client=get_client(cfg.provider),
            model=cfg.model,
            api_key=cfg.api_key,
        )

    @staticmethod
    async def _record_code_arm_unavailable(session: AsyncSession, study: ValidationStudy, evidence: dict) -> None:
        """Say why the authors' code was not run, when there was code to run.

        Silence here would read as "the paper published no code", which is a different and false
        statement. Nothing is recorded for a paper that genuinely published none: step 16's
        `code_absent` already carries that.
        """
        from app.services.code_execution_service import METHOD_AUTHORS_CODE, build_observation
        from app.services.untrusted_execution import UNCONFIGURED_MESSAGE

        resolution = evidence.get("code_resolution") or {}
        if resolution.get("outcome") != "resolved":
            return
        if evidence.get("code_execution"):
            # An arm already ran and owns the record. Overwriting it here would replace a real
            # finding with a statement about this install's configuration.
            return
        evidence["code_execution"] = {
            "attempt": 0,
            "method": METHOD_AUTHORS_CODE,
            "source": {"repo_url": resolution.get("url"), "commit_sha": resolution.get("commit_sha")},
            "session_id": None,
            "outcome": "code_unreachable",
            "qualifiers": [],
            "reason": UNCONFIGURED_MESSAGE,
            "observation": build_observation(
                outcome="code_unreachable", exit_code=None, transcript_uri=None, transcript_tail=""
            ),
        }
        study.evidence_json = dict(evidence)
        await session.flush()

    @staticmethod
    async def _handle_code_arm(session: AsyncSession, study: ValidationStudy, evidence: dict, level3: dict) -> bool:
        """Launch, poll and land the authors' own code. **Every path here reaches a terminal state.**

        The same first-visit / persisted-session-id / poll shape the template arm already uses, with
        one difference that matters: a failure does NOT go through `_degrade_to_level2`. That helper
        writes `evidence["level3_failed"] = {"reason": <prose>}`, which is correct for the template
        arm and destroys the product here: the outcome vocabulary, the transcript and the observation
        record all collapse into one string, and step 19's code section has nothing to render.
        """
        from app.services.code_execution_service import (
            METHOD_AUTHORS_CODE,
            RAN_OUTPUT_DIVERGES,
            RAN_OUTPUT_UNCOMPARABLE,
            adapt_outputs,
            build_observation,
            classify_transcript,
        )

        record = dict(evidence.get("code_execution") or {})
        resolution = evidence.get("code_resolution") or {}

        # `session_id` is the idempotency key, exactly as `level3_run_session_id` is for the template
        # arm. Deliberately NOT that key: a study can carry both results and one key would lose one.
        if not record.get("session_id"):
            entry = evidence.get("code_entry_point") or {}
            entry_point = entry.get("entry_point") or ValidationDriverService._default_entry_point(resolution)
            if not entry_point:
                return await ValidationDriverService._land_code_outcome(
                    session,
                    study,
                    evidence,
                    record={
                        "attempt": 1,
                        "method": METHOD_AUTHORS_CODE,
                        "source": {
                            "repo_url": resolution.get("url"),
                            "commit_sha": resolution.get("commit_sha"),
                        },
                    },
                    observation=build_observation(
                        outcome="code_incomplete",
                        exit_code=None,
                        transcript_uri=None,
                        transcript_tail="",
                    ),
                    reason="no script in the fetched code could be identified as the analysis entry point",
                )

            try:
                cs = await NotebookExecutionService.execute_fetched_code(
                    session,
                    org_id=study.organization_id,
                    user_id=study.requested_by_user_id,
                    code_uri=resolution.get("archive_uri") or resolution.get("url") or "",
                    entry_point=entry_point,
                    arguments=entry.get("arguments") or "",
                    input_file_ids=level3.get("input_file_ids") or [],
                    experiment_id=study.experiment_id,
                )
            except ValidationError as exc:
                # The install cannot run untrusted code. A true statement about this bioAF, not
                # about the paper, so it is recorded and the template arm still runs.
                return await ValidationDriverService._land_code_outcome(
                    session,
                    study,
                    evidence,
                    record={"attempt": 1, "method": METHOD_AUTHORS_CODE, "source": {}},
                    observation=build_observation(
                        outcome="code_unreachable", exit_code=None, transcript_uri=None, transcript_tail=str(exc)
                    ),
                    reason=str(exc),
                    advance=False,
                )

            evidence["code_execution"] = {
                "attempt": 1,
                "method": METHOD_AUTHORS_CODE,
                "source": {"repo_url": resolution.get("url"), "commit_sha": resolution.get("commit_sha")},
                "session_id": cs.id,
                "entry_point": entry_point,
            }
            study.evidence_json = dict(evidence)
            await session.flush()
            return True

        cs = await ValidationDriverService._load_compute_session(session, record["session_id"])
        if cs is None:
            return await ValidationDriverService._land_code_outcome(
                session,
                study,
                evidence,
                record=record,
                observation=build_observation(
                    outcome="code_error", exit_code=None, transcript_uri=None, transcript_tail=""
                ),
                reason="the execution session could not be found",
            )

        cs = await NotebookExecutionService.poll_execution(session, cs)
        if getattr(cs, "status", None) not in ("completed", "failed"):
            return False  # still running

        # The pod writes its log to `/outputs/transcript.txt`, so for a run that COMPLETED the
        # transcript arrives as an output file rather than on the session. Reading only
        # `failure_message` would classify a script that raised (and still exited its pod cleanly)
        # from an empty string, and then list its own log as an uncomparable result: two of the four
        # findings step 19 insists on telling apart, swapped.
        outputs = await ValidationDriverService._read_code_outputs(session, cs)
        transcript = str(getattr(cs, "failure_message", "") or "")
        results = []
        for out in outputs:
            if str(out.get("path", "")).rsplit("/", 1)[-1] == _TRANSCRIPT_FILENAME:
                transcript = f"{transcript}\n{out.get('text') or ''}".strip()
            else:
                results.append(out)

        exit_code = 1 if cs.status == "failed" else 0
        established = classify_transcript(transcript, exit_code=exit_code)
        if established:
            return await ValidationDriverService._land_code_outcome(
                session,
                study,
                evidence,
                record=record,
                observation=build_observation(
                    outcome=established,
                    exit_code=exit_code,
                    transcript_uri=getattr(cs, "gcs_output_prefix", None),
                    transcript_tail=transcript,
                ),
                reason=transcript,
            )

        # The log is the ARGUMENT behind the outcome, not a result the run produced, so it never
        # reaches the output adapters. A run whose only output is its own log wrote nothing.
        adapted = adapt_outputs(outputs=results, claims=evidence.get("comparison_targets") or [])
        if adapted.get("outcome"):
            return await ValidationDriverService._land_code_outcome(
                session,
                study,
                evidence,
                record=record,
                observation=build_observation(
                    outcome=adapted["outcome"],
                    exit_code=exit_code,
                    transcript_uri=getattr(cs, "gcs_output_prefix", None),
                    transcript_tail=transcript,
                    unmatched=adapted.get("unmatched"),
                ),
                reason=adapted["reason"],
            )

        # The run produced something bioAF CAN compare, so COMPARE it. The outcome is decided from
        # the comparison, never from the fact that a file was written: reporting agreement because a
        # table exists would be a claim of agreement made without comparing, which is the defect step
        # 9 exists to prevent one layer up.
        paper_value = our_value = None
        metric = None
        if adapted["kind"] == "differential_table":
            evidence["code_output_table"] = {"path": adapted["path"], "text": adapted["table_text"]}
            # The SAME concordance the template arm scores, on the paper's own set. Do not build a
            # second comparator.
            # The generated arm runs precisely when bioAF could not wire a level3 bundle, so the
            # paper's own confirmed set comes off the PLAN there. Scoring that comparison is the
            # whole reason that arm exists: without it the study still produces nothing.
            target = level3 or await ValidationDriverService._finding_target_from_plan(session, study)
            params = target.get("parameters") or {}
            from app.services.validation_claim_cutoffs import normalizer_arguments

            applied = normalizer_arguments(target.get("cutoffs"), legacy=params)
            if applied is None:
                # change_7.4 section 1.6: no cutoff is supplied. A table the paper's definition cannot
                # be applied to is an output bioAF could not compare, and the reason is recorded.
                return await ValidationDriverService._land_code_outcome(
                    session,
                    study,
                    evidence,
                    record=record,
                    observation=build_observation(
                        outcome=RAN_OUTPUT_UNCOMPARABLE,
                        exit_code=exit_code,
                        transcript_uri=getattr(cs, "gcs_output_prefix", None),
                        transcript_tail=transcript,
                        unmatched=adapted.get("unmatched"),
                    ),
                    reason=target.get("threshold_refusal") or "the comparison's statistical cutoff is not established",
                )
            our_fs = (normalize_interval_table if target.get("kind") == "interval" else normalize_gene_table)(
                adapted["table_text"], **applied
            )
            paper_fs = FindingSet.from_dict(target.get("paper_finding_set") or {})
            universe = int(
                target.get("universe") or our_fs.n_tested or max(len(paper_fs.entities), len(our_fs.entities), 1)
            )
            compare = compare_interval_sets if target.get("kind") == "interval" else compare_gene_sets
            conc = compare(paper_fs, our_fs, universe)
            evidence["level3_result"] = {
                "concordance": conc.to_dict(),
                "our_finding_set": our_fs.to_dict(),
                # Which analysis produced it. A concordance scored against the authors' OWN code is
                # a stronger claim than one scored against ours, and the verdict has to say so.
                "method": record.get("method"),
            }
            outcome = "ran_output_agrees" if conc.verdict == "agree" else RAN_OUTPUT_DIVERGES
            metric = "differentially expressed features"
            paper_value, our_value = len(paper_fs.entities), len(our_fs.entities)
        elif adapted["kind"] == "unsupported":
            outcome = RAN_OUTPUT_UNCOMPARABLE
        else:
            merged = dict(evidence.get("computed_metrics") or {})
            merged.update(adapted["computed_metrics"])
            evidence["computed_metrics"] = merged
            # A matched numeric claim goes to the existing claim/tolerance machinery at `comparing`,
            # which is what decides agreement. Until it has, the run has produced a comparable
            # result and nothing more.
            outcome = "ran_output_agrees"

        record.update(
            observation=build_observation(
                outcome=outcome,
                exit_code=exit_code,
                transcript_uri=getattr(cs, "gcs_output_prefix", None),
                transcript_tail=transcript,
                unmatched=adapted.get("unmatched"),
                metric=metric,
                paper_value=paper_value,
                our_value=our_value,
            ),
            outcome=outcome,
            comparable=True,
            output_kind=adapted["kind"],
        )
        evidence["code_execution"] = record
        study.evidence_json = dict(evidence)
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "comparing"
        )
        return True

    @staticmethod
    async def _finding_target_from_plan(session: AsyncSession, study: ValidationStudy) -> dict:
        """The paper's confirmed finding set and thresholds, in the shape `level3` carries them.

        Used only by the generated arm, which runs when bioAF could not build a level3 bundle at
        all. The claim itself is still on the plan; what was missing was a route from OUR wiring to
        it. Returns an empty target when the paper confirmed no set, and the comparison then scores
        nothing rather than scoring against an absence.
        """
        from app.services.validation_claim_cutoffs import resolve_analysis_thresholds

        plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
        claim = (plan.finding_claim_json if plan else None) or {}
        design = (plan.differential_design_json if plan else None) or {}
        selected, _ = selected_contrast_for(
            design,
            pipeline_key=plan.pipeline_key if plan else None,
            library_strategy=plan.library_strategy if plan else None,
        )
        contrast = (design.get("contrasts") or [])[selected] if selected is not None else None
        # change_7.4 section 1.6: the cutoffs the paper's set is defined at, never a default. Where
        # they cannot be applied the target says why, and the comparison is not scored.
        cutoffs = resolve_analysis_thresholds(claim, design, contrast)
        target = {
            "kind": claim.get("kind") or "gene",
            "paper_finding_set": claim.get("finding_set") or {},
            "parameters": {},
        }
        if cutoffs["refusal"]:
            target["threshold_refusal"] = cutoffs["refusal"]
        else:
            from app.services.validation_claim_cutoffs import recorded_cutoffs

            target["parameters"] = {
                "lfc_threshold": cutoffs["lfc_threshold"],
                "padj_threshold": cutoffs["padj_threshold"],
            }
            target["cutoffs"] = recorded_cutoffs(cutoffs)
        return target

    @staticmethod
    def _default_entry_point(resolution: dict) -> str | None:
        """The obvious entry point when nothing chose one: a lone script.

        A repository with one analysis file needs no model call, and asking for one would spend a
        request to answer a question with one possible answer.
        """
        scripts = [
            f["path"]
            for f in resolution.get("files") or []
            if str(f.get("path", "")).lower().endswith((".r", ".py", ".ipynb", ".rmd", ".sh"))
        ]
        return scripts[0] if len(scripts) == 1 else None

    @staticmethod
    async def _land_code_outcome(
        session: AsyncSession,
        study: ValidationStudy,
        evidence: dict,
        *,
        record: dict,
        observation: dict,
        reason: str,
        advance: bool = True,
    ) -> bool:
        """Record a terminal code-arm outcome with its full record, and advance.

        **A code-arm failure is a terminal OUTCOME that carries its record**, not a degrade. Level-2
        evidence is still preserved, by the same additive rule `_degrade_to_level2` documents; what
        changes is that the reason is structured rather than prose.
        """
        record = {**record, "outcome": observation["outcome"], "observation": observation, "reason": reason}
        record.setdefault("qualifiers", [])
        evidence["code_execution"] = record
        study.evidence_json = dict(evidence)
        logger.info("validation study %s: code arm finished as %s (%s)", study.id, observation["outcome"], reason[:200])
        if not advance:
            return False
        await ValidationStudyService.transition(
            session, study.id, study.organization_id, study.requested_by_user_id, "comparing"
        )
        return True

    @staticmethod
    async def _read_code_outputs(session: AsyncSession, cs) -> list[dict]:
        """Everything the fetched code wrote, as ``{path, text}``. Live seam; mocked in unit tests.

        Reads the same `notebook_session_files` join the template arm's output reader uses, so a run
        whose outputs were registered normally is readable without a second mechanism.
        """
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
        storage = get_storage_adapter()
        outputs: list[dict] = []
        for row in rows:
            try:
                text = await storage.read_text(row.storage_uri)
            except Exception:  # noqa: BLE001 - a file we cannot read is still a file the run wrote
                text = ""
            outputs.append({"path": row.filename or row.storage_uri, "text": text})
        return outputs

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
            # change_7.5 section 1.2: the reproduction applied the paper's definition when its bundle
            # records the cutoffs it was filtered at, which are only ever the stated ones. The paper-
            # level pair was the test before, and a plan whose claims state their own cutoffs has none.
            applied = (evidence.get("level3") or {}).get("cutoffs") or {}
            params = (evidence.get("level3") or {}).get("parameters") or {}
            design = (plan.differential_design_json if plan else None) or {}
            th = design.get("thresholds") or {}
            differential_attribution = {
                "thresholds_matched": isinstance(applied.get("significance"), dict)
                or (params.get("lfc_threshold") is not None and params.get("padj_threshold") is not None)
                or (th.get("log2fc") is not None and th.get("padj") is not None),
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
            # plan_7 step 9: which KIND of validation this was. A deposit-route verdict tests the
            # authors' statistics rather than their processing, and the verdict has to say so.
            route=evidence.get("route"),
            # plan_7 step 17: the method the verdict was actually reached by. The code arm outranks
            # bioAF's own templates, which outrank an analysis generated from prose, so the verdict
            # can say which claim it is making. The ladder's choice wins over the bundle's default.
            reproduction_method=(
                (evidence.get("code_execution") or {}).get("method") or (evidence.get("level3") or {}).get("method")
            ),
            # Passed as SEPARATE inputs, never as a pre-collapsed token that has already decided the
            # cause: the classifier may reason from "diverged, cause unresolved, candidates include
            # our input mapping" and from "agreed, but generated from a description assessed as
            # inadequate", and it must not be handed a conclusion dressed as evidence.
            code_outcome=(evidence.get("code_execution") or {}).get("outcome"),
            code_qualifiers=(evidence.get("code_execution") or {}).get("qualifiers"),
        )
        evidence["classification_result"] = result

        # plan_7 step 17: was this noise read as signal? Asked only after an arm that RAN CODE
        # diverged, and carried BESIDE the verdict as a hedged possible issue, never as the verdict.
        # The tool never concludes the authors got it wrong.
        assessment = await ValidationDriverService._assess_execution(session, study, evidence, result)
        if assessment:
            evidence.update(assessment)

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
    async def _assess_execution(
        session: AsyncSession, study: ValidationStudy, evidence: dict, result: dict
    ) -> dict | None:
        """plan_7 step 17's two hedged calls, when an execution arm diverged. None otherwise.

        Both are scoped to the arms that ran code: only they give a like-for-like result to set
        against the paper's own. A pipeline-route divergence like study 26's 7,389 vs 4,054 peaks
        still gets prose only, deliberately.

        **Evidence is passed explicitly**, not re-read off the study, per plan_7 defect 4 and the
        ordering trap `_handle_extracting` documents at length.
        """
        from app.services.signal_assessment import assess_causes, assess_signal

        execution = evidence.get("code_execution") or {}
        observation = execution.get("observation") or {}
        method = execution.get("method") or ""
        if not method:
            return None

        diverged = next(
            (c for c in result.get("comparisons") or [] if c.get("verdict") == "diverge"),
            None,
        )
        if observation.get("paper_value") is None and diverged:
            observation = {
                **observation,
                "paper_value": diverged.get("claimed_normalized") or diverged.get("claimed_value"),
                "our_value": diverged.get("computed_value"),
                "metric": diverged.get("mapped_key") or diverged.get("metric_key"),
            }

        cfg = await llm_provider_config_service.get_for_feature(
            session, study.organization_id, FEATURE_LITERATURE_VALIDATION
        )
        if cfg is None:
            return None
        client = get_client(cfg.provider)

        out: dict = {}
        signal = await assess_signal(
            method=method,
            paper_value=observation.get("paper_value"),
            our_value=observation.get("our_value"),
            metric=observation.get("metric"),
            context=str(result.get("reasoning") or "")[:1500],
            client=client,
            model=cfg.model,
            api_key=cfg.api_key,
        )
        if signal:
            # Its own top-level key, matching `capabilities`, `precompute_checks`,
            # `deposit_selection` and `level3`, so step 19 reads one place whichever arm ran.
            out["signal_assessment"] = signal

        causes = await assess_causes(
            observation=observation, method=method, client=client, model=cfg.model, api_key=cfg.api_key
        )
        if causes:
            # Stored apart from the observation, so a candidate explanation is never mistaken for
            # something the run established.
            out["execution_assessment"] = causes
        return out or None

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

        issues: list[dict] = []
        decision = await ratify(
            result,
            autonomy=autonomy,
            client=get_client(cfg.provider),
            model=cfg.model,
            api_key=cfg.api_key,
            on_issue=issues.append,
        )
        await ValidationIssueService.record(session, study, issues)
        return decision

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
    async def _launch_fetchngs(session: AsyncSession, study: ValidationStudy, *, claim=None) -> bool:
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

        # change_7.2 section 2: fencing a database write does not recall a pipeline run that has
        # already launched. The operation's identity is persisted BEFORE the dispatch, reused across
        # retries, and a run a previous worker started is adopted rather than replaced.
        adopted = await ValidationDriverService._adopt_running_operation(session, study, _OP_DATA_ACQUISITION)
        if adopted is not None:
            return adopted

        launch = PipelineRunLaunchRequest(
            pipeline_key=_FETCHNGS_KEY,
            experiment_id=study.experiment_id,
            parameters={"accessions": accessions, "download_method": _FETCHNGS_DOWNLOAD_METHOD},
        )
        await begin_operation(session, study, _OP_DATA_ACQUISITION, claim=claim, kind="nf-core/fetchngs")
        await assert_held(session, claim)
        run = await ValidationDriverService._launch(session, study, launch)
        study.data_run_id = run.id
        await record_dispatch(session, study, _OP_DATA_ACQUISITION, external_id=run.id, claim=claim)
        if run.status in _RUN_FAILED:
            return await ValidationDriverService._fail(session, study, "data acquisition run failed to launch")
        return True  # stays in acquiring_data until the fetch completes

    @staticmethod
    async def _adopt_running_operation(session: AsyncSession, study: ValidationStudy, key: str) -> bool | None:
        """Take over an external operation a previous worker started, or ask before doing so.

        Returns True when this tick's work is done (adopted, or waiting for an answer), None when
        there is nothing to adopt and the caller should dispatch.

        A worker can launch a run and crash before recording its identifier, and its replacement must
        never launch a second one. An autonomous organization adopts and records the adoption; an
        assisted one is asked whether to resume it or start a new one.
        """
        record = adoptable(study, key)
        if record is None:
            return None

        org = await session.get(Organization, study.organization_id)
        autonomy = (org.lit_validation_autonomy if org else None) or AUTONOMY_ASSISTED
        if autonomy != AUTONOMY_AUTONOMOUS:
            evidence = dict(study.evidence_json or {})
            if not evidence.get("awaiting_adoption"):
                logger.info("validation study %d: an earlier %s may still be running; asking", study.id, key)
            evidence["awaiting_adoption"] = {
                "operation": key,
                "operation_id": record.get("operation_id"),
                "external_id": record.get("external_id"),
                "at": _now().isoformat(),
                "action": "resume the run that is already going, or cancel it and start a new one",
            }
            study.evidence_json = evidence
            await session.flush()
            return True

        await adopt(session, study, key, by="driver")
        if record.get("external_id"):
            if key == _OP_DATA_ACQUISITION and study.data_run_id is None:
                study.data_run_id = record["external_id"]
            elif key == _OP_ANALYSIS and study.analysis_run_id is None:
                study.analysis_run_id = record["external_id"]
        logger.info("validation study %d: adopted the running %s rather than launching a second", study.id, key)
        await session.flush()
        return True

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
        significance_kind: str = "padj",
        significance_operator: str = "<=",
        effect_operator: str = ">=",
    ) -> FindingSet:
        """Read the normalized result table the differential notebook wrote (a registered output
        File) and normalize it into OUR FindingSet. Applies the paper's captured thresholds (passed
        from the plan) so our set is defined by the same cutoffs as the paper's set (E3': a threshold
        mismatch is an our-side effect, not a real divergence). Live seam (reads object storage);
        mocked in unit tests.

        change_7.5 section 1.2: the measure and the operators are the stated ones. Every production
        caller passes all five; the defaults are the legacy pair's reading."""
        text = await ValidationDriverService._read_reproduction_output(session, cs)
        if not text:
            ns = "interval" if kind == "interval" else "unknown"
            return FindingSet(kind=kind, namespace=ns, parse_notes=["no reproduction output found"])
        applied = {
            "lfc_threshold": lfc_threshold,
            "padj_threshold": padj_threshold,
            "significance_kind": significance_kind,
            "significance_operator": significance_operator,
            "effect_operator": effect_operator,
        }
        if kind == "interval":
            return normalize_interval_table(text, **applied)
        return normalize_gene_table(text, **applied)

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
