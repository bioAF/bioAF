"""Lab Glossary scan + proposal-review service (ADR-062).

A glossary scan is an in-process async job, mirroring the Agent Review pattern
(``agent_review_job_service.execute_hosted``): a ``lab_glossary_scan_jobs`` row is
created ``pending``, dispatched via FastAPI ``BackgroundTasks`` (the API layer),
and runs to ``complete``/``failed`` in its own DB session. The LLM call goes
through the provider abstraction (``get_client(provider).submit``); source-content
fetching and the LLM call are injectable so the logic is testable without GCS/LLM.

CSV import (``parse_csv_import``) produces the same ``lab_glossary_scan_proposals``
rows synchronously (no LLM) and flows through the identical review path.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.lab_glossary import (
    LabGlossaryRejectedProposal,
    LabGlossaryScanJob,
    LabGlossaryScanProposal,
)
from app.services import audit_service, llm_provider_config_service
from app.services.lab_glossary_service import LabGlossaryService
from app.services.llm_provider_clients import get_client
from app.services.notification_channels.in_app import InAppChannel

logger = logging.getLogger("bioaf.lab_glossary_scan")

MAX_IMPORT_ROWS = 500

# Prompt the model to return a strict JSON array of term objects. Kept short and
# explicit so the response parser stays simple.
SCAN_PROMPT = (
    "You are building a glossary of lab-specific terminology for a biotech lab. "
    "From the provided content, extract domain-specific terms and concise definitions "
    "appropriate for this lab's context. Return ONLY a JSON array; each element is an "
    'object with keys: "term", "definition", and optionally "aliases" (array of strings), '
    '"category", "context". Do not include commentary outside the JSON array.'
)


class CsvParseError(Exception):
    """Raised when an uploaded CSV/TSV is malformed or violates import limits."""


# Valid sources for a NEW LLM scan job (LK-SPEC-D, F-LKD-01). ``topic`` is gone:
# it produced speculative, ungrounded terms, replaced by ``experiment``. ``import``
# is created via ``parse_csv_import``, not here. Historical ``topic`` rows remain
# readable (the CHECK constraint is widened, not replaced).
VALID_SCAN_TYPES = ("experiment", "document", "platform_wide")

# Source stores a ``document`` scan can target (LK-SPEC-D, F-LKD-03).
DOCUMENT_SOURCES = ("lab_document", "file")


def _parse_document_input(scan_input: str | None) -> tuple[str, int]:
    """Resolve a ``document`` scan_input to ``(source, id)``.

    Accepts ``lab_document:<id>``, ``file:<id>``, or a bare ``<int>`` (treated as
    a Lab Knowledge document for back-compat). Raises ``ValidationError`` on an
    unknown prefix or non-numeric id so the API can reject it up front (AC-D06)."""
    raw = (scan_input or "").strip()
    if not raw:
        raise ValidationError("document scan requires a scan_input")
    if ":" in raw:
        source, _, ident = raw.partition(":")
        if source not in DOCUMENT_SOURCES:
            raise ValidationError(f"unknown document source prefix: {source}")
    else:
        source, ident = "lab_document", raw
    if not ident.isdigit():
        raise ValidationError(f"document scan_input must reference a numeric id: {scan_input!r}")
    return source, int(ident)


# --- scan job lifecycle ------------------------------------------------------


async def create_scan_job(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    scan_type: str,
    scan_input: str | None = None,
) -> LabGlossaryScanJob:
    """Create a pending scan job. The API dispatches execution as a background task.

    ``scan_input`` is validated up front by type so a bad reference fails at
    creation rather than inside the background task (AC-D06)."""
    if scan_type not in VALID_SCAN_TYPES:
        raise ValidationError(f"invalid scan_type for LLM scan: {scan_type}")
    if scan_type == "experiment":
        if not (scan_input or "").strip().isdigit():
            raise ValidationError("experiment scan requires a numeric experiment id")
    elif scan_type == "document":
        _parse_document_input(scan_input)  # raises ValidationError on bad input
    job = LabGlossaryScanJob(
        organization_id=org_id,
        scan_type=scan_type,
        scan_input=scan_input,
        status="pending",
        initiated_by_user_id=user_id,
    )
    session.add(job)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="lab_glossary_scan_job",
        entity_id=job.id,
        action="initiated",
        details={"scan_type": scan_type, "scan_input": scan_input},
    )
    await session.flush()
    return job


async def execute_scan(
    session_factory,
    *,
    job_id: int,
    content_provider=None,
    submit_override=None,
) -> None:
    """Run a glossary scan to completion in its own session.

    Owns its DB session so it can run as a background task outside the request
    lifespan. ``content_provider(session, job)`` returns the source text; defaults
    to ``_fetch_source_content``. ``submit_override`` replaces the LLM call (kwargs
    prompt/payload/model/api_key) so tests need neither a provider nor a network.
    Any failure transitions the job to ``failed`` with ``error_message`` and still
    notifies the initiator, so a scan never strands in ``running``.
    """
    content_provider = content_provider or _fetch_source_content
    try:
        await _execute_scan_inner(
            session_factory, job_id=job_id, content_provider=content_provider, submit_override=submit_override
        )
    except Exception as exc:  # noqa: BLE001 - last-resort guard; nothing re-raised
        logger.exception("execute_scan unhandled error: job_id=%d", job_id)
        await _fail_job(session_factory, job_id, str(exc))


async def _execute_scan_inner(session_factory, *, job_id, content_provider, submit_override) -> None:
    async with session_factory() as session:
        job = (await session.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.id == job_id))).scalar_one()
        job.status = "running"
        await session.flush()

        # Resolve provider. With a submit_override we do not require a configured
        # provider (tests, and any future stubbed dispatch).
        provider_cfg = await llm_provider_config_service.get_active(session, job.organization_id)
        if submit_override is not None:
            submit_callable = submit_override
            model = provider_cfg.model if provider_cfg else "test-model"
            api_key = provider_cfg.api_key if provider_cfg else None
        else:
            if provider_cfg is None or not provider_cfg.model:
                raise RuntimeError("no active LLM provider configured for this org")
            submit_callable = get_client(provider_cfg.provider).submit
            model = provider_cfg.model
            api_key = provider_cfg.api_key

        payload = await content_provider(session, job)
        prompt = SCAN_PROMPT
        await session.commit()

    # LLM call outside any open transaction.
    response_text = await submit_callable(prompt=prompt, payload=payload, model=model, api_key=api_key)
    extracted = _parse_llm_terms(response_text)

    async with session_factory() as session:
        job = (await session.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.id == job_id))).scalar_one()
        new_count, changed_count = await _build_proposals(session, job=job, extracted=extracted)
        job.status = "complete"
        job.proposed_new_count = new_count
        job.proposed_changed_count = changed_count
        job.completed_at = datetime.now(UTC)
        await audit_service.log_action(
            session,
            user_id=job.initiated_by_user_id,
            entity_type="lab_glossary_scan_job",
            entity_id=job.id,
            action="completed",
            details={"proposed_new": new_count, "proposed_changed": changed_count},
        )
        await InAppChannel.deliver(
            session,
            org_id=job.organization_id,
            user_id=job.initiated_by_user_id,
            event_type="lab_glossary_scan_complete",
            title="Glossary scan complete",
            message=f"Found {new_count} new and {changed_count} changed term(s) to review.",
            severity="info",
            metadata={"scan_job_id": job.id},
        )
        await session.commit()


async def _fail_job(session_factory, job_id: int, error: str) -> None:
    try:
        async with session_factory() as session:
            job = (
                await session.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.id == job_id))
            ).scalar_one_or_none()
            if job is None or job.status in ("complete", "failed"):
                return
            job.status = "failed"
            job.error_message = error[:4000]
            job.completed_at = datetime.now(UTC)
            await audit_service.log_action(
                session,
                user_id=job.initiated_by_user_id,
                entity_type="lab_glossary_scan_job",
                entity_id=job.id,
                action="failed",
                details={"error": error[:1000]},
            )
            await InAppChannel.deliver(
                session,
                org_id=job.organization_id,
                user_id=job.initiated_by_user_id,
                event_type="lab_glossary_scan_failed",
                title="Glossary scan failed",
                message="The glossary scan could not be completed.",
                severity="error",
                metadata={"scan_job_id": job.id},
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - never raise from the guard
        logger.exception("failed to mark glossary scan job failed: job_id=%d", job_id)


async def mark_orphaned_on_startup(session: AsyncSession) -> int:
    """Fail any scan job left in-flight by a process restart (ADR-062), mirroring
    agent_review_job_service.mark_orphaned_on_startup."""
    rows = (
        (await session.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.status.in_(("pending", "running")))))
        .scalars()
        .all()
    )
    for job in rows:
        job.status = "failed"
        job.error_message = "API process restarted while scan was in flight."
        job.completed_at = datetime.now(UTC)
    await session.flush()
    return len(rows)


# --- CSV import --------------------------------------------------------------


async def parse_csv_import(session: AsyncSession, *, org_id: int, user_id: int, content: str) -> LabGlossaryScanJob:
    """Parse CSV/TSV into proposals under a new ``import`` scan job. Required
    columns: term, definition. Optional: aliases (pipe- or comma-delimited within
    the cell), category, context. Unrecognized columns are ignored."""
    rows = _parse_delimited(content)
    if not rows:
        raise CsvParseError("File is empty or has no data rows")
    if len(rows) > MAX_IMPORT_ROWS:
        raise CsvParseError(f"Import exceeds the {MAX_IMPORT_ROWS}-row limit ({len(rows)} rows)")

    extracted: list[dict] = []
    for row in rows:
        term = (row.get("term") or "").strip()
        definition = (row.get("definition") or "").strip()
        if not term or not definition:
            continue
        extracted.append(
            {
                "term": term,
                "definition": definition,
                "aliases": _split_aliases(row.get("aliases")),
                "category": (row.get("category") or "").strip() or None,
                "context": (row.get("context") or "").strip() or None,
            }
        )

    job = LabGlossaryScanJob(
        organization_id=org_id,
        scan_type="import",
        scan_input=None,
        status="running",
        initiated_by_user_id=user_id,
    )
    session.add(job)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="lab_glossary_scan_job",
        entity_id=job.id,
        action="initiated",
        details={"scan_type": "import", "rows": len(extracted)},
    )
    new_count, changed_count = await _build_proposals(session, job=job, extracted=extracted)
    job.status = "complete"
    job.proposed_new_count = new_count
    job.proposed_changed_count = changed_count
    job.completed_at = datetime.now(UTC)
    await session.flush()
    return job


def _parse_delimited(content: str) -> list[dict]:
    sample = content[:2048]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    if reader.fieldnames is None:
        raise CsvParseError("File has no header row")
    headers = {(h or "").strip().lower() for h in reader.fieldnames}
    if "term" not in headers or "definition" not in headers:
        raise CsvParseError("Required columns 'term' and 'definition' are missing")
    out: list[dict] = []
    for raw in reader:
        out.append({(k or "").strip().lower(): v for k, v in raw.items()})
    return out


def _split_aliases(value: str | None) -> list[str] | None:
    if not value:
        return None
    parts = [p.strip() for p in value.replace("|", ",").split(",")]
    parts = [p for p in parts if p]
    return parts or None


# --- proposal building (shared by scan + import) -----------------------------


async def _build_proposals(session: AsyncSession, *, job: LabGlossaryScanJob, extracted: list[dict]) -> tuple[int, int]:
    """Write proposals, deduping against committed terms and flagging
    previously-rejected ones. Returns (new_count, changed_count)."""
    new_count = 0
    changed_count = 0
    seen: set[str] = set()
    for item in extracted:
        term = (item.get("term") or "").strip()
        definition = (item.get("definition") or "").strip()
        if not term or not definition:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)

        existing = await LabGlossaryService.get_by_term(session, org_id=job.organization_id, term=term)
        if existing is not None and existing.definition.strip() == definition:
            continue  # unchanged -> skip entirely
        proposal_type = "changed" if existing is not None else "new"

        rejected_before = (
            await session.execute(
                select(func.count())
                .select_from(LabGlossaryRejectedProposal)
                .where(
                    LabGlossaryRejectedProposal.organization_id == job.organization_id,
                    func.lower(LabGlossaryRejectedProposal.term) == key,
                    LabGlossaryRejectedProposal.proposed_source == job.scan_type,
                )
            )
        ).scalar() or 0

        session.add(
            LabGlossaryScanProposal(
                scan_job_id=job.id,
                term=term,
                proposed_definition=definition,
                proposed_aliases=_coerce_aliases(item.get("aliases")),
                proposed_category=(item.get("category") or None),
                proposed_context=(item.get("context") or None),
                proposal_type=proposal_type,
                existing_term_id=existing.id if existing is not None else None,
                source_description=item.get("source_description"),
                previously_rejected=rejected_before > 0,
            )
        )
        if proposal_type == "new":
            new_count += 1
        else:
            changed_count += 1
    await session.flush()
    return new_count, changed_count


def _coerce_aliases(value) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
        return items or None
    return _split_aliases(str(value))


def _parse_llm_terms(text: str) -> list[dict]:
    """Extract the JSON array of term objects from an LLM response, tolerating
    markdown code fences and an optional wrapping object."""
    if not text:
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("could not parse LLM glossary response as JSON")
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("terms") or parsed.get("results") or []
    if not isinstance(parsed, list):
        return []
    return [p for p in parsed if isinstance(p, dict)]


async def _fetch_source_content(session: AsyncSession, job: LabGlossaryScanJob) -> str:
    """Default source-content fetch by scan type. Document and platform-wide
    fetching reuse existing extraction utilities; experiment reuses the Experiment
    Review context builder."""
    if job.scan_type == "experiment":
        return await _collect_experiment_content(session, job)
    if job.scan_type == "document":
        return await _extract_document_text(session, job)
    if job.scan_type == "platform_wide":
        return await _collect_platform_content(session, job)
    return ""


async def _collect_experiment_content(session: AsyncSession, job: LabGlossaryScanJob) -> str:
    """Assemble the same material the AI Experiment Review reads (LK-SPEC-D, F-LKD-02).

    Reuses ``agent_review_artifact_builder``: the experiment header (fields +
    samples) plus, for every pipeline run on the experiment, the run metadata,
    run samples, and QC dashboard. It does NOT read the experiment's associated
    files; that matches Experiment Review and keeps the never-ship contract intact.

    Raises ``NotFoundError`` if the experiment is missing or in another org so the
    job fails cleanly with a clear error (AC-D04)."""
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun
    from app.services.agent_review_artifact_builder import (
        ArtifactBuildError,
        build_experiment_header,
        build_for_run,
    )

    experiment_id = int(job.scan_input)
    exp = (
        await session.execute(
            select(Experiment).where(Experiment.id == experiment_id, Experiment.organization_id == job.organization_id)
        )
    ).scalar_one_or_none()
    if exp is None:
        raise NotFoundError(f"experiment {experiment_id} not found in this organization")

    # All pipeline runs on the experiment feed the scan (LK-SPEC-D, OQ-2: all runs).
    run_ids = list(
        (
            await session.execute(
                select(PipelineRun.id).where(PipelineRun.experiment_id == exp.id).order_by(PipelineRun.id.asc())
            )
        )
        .scalars()
        .all()
    )

    chunks: list[str] = [await build_experiment_header(session, experiment_id=exp.id, included_run_ids=run_ids)]
    for run_id in run_ids:
        try:
            artifact = await build_for_run(session, run_id)
        except ArtifactBuildError:
            # A run with no shippable output (e.g. still running) contributes
            # nothing rather than failing the whole scan.
            continue
        chunks.append(artifact.markdown)
    return "\n\n".join(c for c in chunks if c)


async def _extract_document_text(session: AsyncSession, job: LabGlossaryScanJob) -> str:
    """Extract text from the document a ``document`` scan targets (LK-SPEC-D, F-LKD-03).

    Dispatches on the ``scan_input`` source: ``lab_document:<id>`` (or a bare int)
    reads the Lab Knowledge document's current version; ``file:<id>`` reads a
    Data & Files ``File``. Both org-scope the row and reuse ``extract_text_from_gcs``.
    A missing/foreign row contributes empty text (the scan does not crash)."""
    source, ident = _parse_document_input(job.scan_input)
    if source == "file":
        gcs_uri = await _file_gcs_uri(session, ident, job.organization_id)
    else:
        gcs_uri = await _lab_document_gcs_uri(session, ident, job.organization_id)
    if gcs_uri is None:
        return ""
    from app.services.lab_glossary_extraction import extract_text_from_gcs

    return await extract_text_from_gcs(session, gcs_uri)


async def _lab_document_gcs_uri(session: AsyncSession, doc_id: int, org_id: int) -> str | None:
    from app.models.lab_document import LabDocument, LabDocumentVersion

    doc = (
        await session.execute(
            select(LabDocument).where(LabDocument.id == doc_id, LabDocument.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if doc is None:
        return None
    version = (
        await session.execute(
            select(LabDocumentVersion).where(
                LabDocumentVersion.document_id == doc.id,
                LabDocumentVersion.version_number == doc.current_version,
            )
        )
    ).scalar_one_or_none()
    return version.storage_uri if version is not None else None


async def _file_gcs_uri(session: AsyncSession, file_id: int, org_id: int) -> str | None:
    from app.models.file import File

    f = (
        await session.execute(select(File).where(File.id == file_id, File.organization_id == org_id))
    ).scalar_one_or_none()
    return f.storage_uri if f is not None else None


async def _collect_platform_content(session: AsyncSession, job: LabGlossaryScanJob) -> str:
    """Gather org content into a single chunkable payload: experiment
    names/hypotheses/descriptions, sample metadata, and pipeline run names.

    SDR text and Lab Knowledge document bodies are intentionally not collected
    here yet (SDRs land in Phase C; per-document GCS extraction is deferred to
    avoid fanning out downloads on every platform scan)."""
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun
    from app.models.sample import Sample

    chunks: list[str] = []

    org_exp_ids = select(Experiment.id).where(Experiment.organization_id == job.organization_id)

    exps = (
        await session.execute(
            select(Experiment.name, Experiment.hypothesis, Experiment.description).where(
                Experiment.organization_id == job.organization_id
            )
        )
    ).all()
    for name, hypothesis, description in exps:
        chunks.append(" | ".join([p for p in (name, hypothesis, description) if p]))

    samples = (
        await session.execute(
            select(Sample.organism, Sample.tissue_type, Sample.treatment_condition).where(
                Sample.experiment_id.in_(org_exp_ids)
            )
        )
    ).all()
    for organism, tissue, treatment in samples:
        chunks.append(" | ".join([p for p in (organism, tissue, treatment) if p]))

    runs = (
        await session.execute(
            select(PipelineRun.pipeline_name).where(PipelineRun.organization_id == job.organization_id)
        )
    ).all()
    for (pipeline_name,) in runs:
        if pipeline_name:
            chunks.append(pipeline_name)

    return "\n".join(c for c in chunks if c)


# --- review / commit ---------------------------------------------------------


async def review_proposals(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    job_id: int,
    decisions: dict[int, str] | None = None,
    accept_all_remaining: bool = False,
    reject_all_remaining: bool = False,
) -> dict:
    """Apply per-proposal decisions and commit results.

    ``decisions`` maps proposal id -> 'accepted' | 'rejected' | 'kept_existing'.
    Remaining pending proposals are accepted (``accept_all_remaining``) or rejected
    (``reject_all_remaining``). Accepted 'new' proposals create terms; accepted
    'changed' proposals update the term (prior values into history). Rejected and
    kept-existing proposals are recorded in ``lab_glossary_rejected_proposals`` so
    future scans can flag them. One audit summary row is written per session.
    """
    decisions = decisions or {}
    job = (
        await session.execute(
            select(LabGlossaryScanJob).where(
                LabGlossaryScanJob.id == job_id, LabGlossaryScanJob.organization_id == org_id
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("scan job not found")

    proposals = (
        (
            await session.execute(
                select(LabGlossaryScanProposal).where(
                    LabGlossaryScanProposal.scan_job_id == job_id,
                    LabGlossaryScanProposal.review_status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )

    term_source = "import" if job.scan_type == "import" else "llm_scan"
    counts = {"accepted": 0, "rejected": 0, "kept_existing": 0}

    for prop in proposals:
        decision = decisions.get(prop.id)
        if decision is None:
            if accept_all_remaining:
                decision = "accepted"
            elif reject_all_remaining:
                decision = "rejected"
            else:
                continue
        if decision not in ("accepted", "rejected", "kept_existing"):
            raise ValidationError(f"invalid decision for proposal {prop.id}: {decision}")

        if decision == "accepted":
            await _commit_accepted(session, org_id=org_id, user_id=user_id, prop=prop, term_source=term_source)
        else:
            # rejected and kept_existing both record a rejection so the proposal
            # does not recur silently; kept_existing additionally leaves the
            # current term untouched (it already is).
            session.add(
                LabGlossaryRejectedProposal(
                    organization_id=org_id,
                    term=prop.term,
                    proposed_definition=prop.proposed_definition,
                    proposed_source=job.scan_type,
                    rejected_by_user_id=user_id,
                )
            )
        prop.review_status = decision
        prop.reviewed_by_user_id = user_id
        prop.reviewed_at = datetime.now(UTC)
        counts[decision] += 1

    job.accepted_count = (job.accepted_count or 0) + counts["accepted"]
    job.rejected_count = (job.rejected_count or 0) + counts["rejected"] + counts["kept_existing"]

    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="lab_glossary_proposals_reviewed",
        entity_id=job.id,
        action="proposals_reviewed",
        details={"scan_job_id": job.id, **counts},
    )
    await session.flush()
    return counts


async def _commit_accepted(
    session: AsyncSession, *, org_id: int, user_id: int, prop: LabGlossaryScanProposal, term_source: str
) -> None:
    if prop.proposal_type == "changed" and prop.existing_term_id is not None:
        await LabGlossaryService.update_term(
            session,
            org_id=org_id,
            user_id=user_id,
            term_id=prop.existing_term_id,
            definition=prop.proposed_definition,
            aliases=prop.proposed_aliases,
            category=prop.proposed_category,
            context=prop.proposed_context,
        )
        return
    # 'new' (or 'changed' whose term has since vanished): create.
    existing = await LabGlossaryService.get_by_term(session, org_id=org_id, term=prop.term)
    if existing is not None:
        # Term materialized between scan and review; treat as an update.
        await LabGlossaryService.update_term(
            session,
            org_id=org_id,
            user_id=user_id,
            term_id=existing.id,
            definition=prop.proposed_definition,
            aliases=prop.proposed_aliases,
            category=prop.proposed_category,
            context=prop.proposed_context,
        )
        return
    await LabGlossaryService.create_term(
        session,
        org_id=org_id,
        user_id=user_id,
        term=prop.term,
        definition=prop.proposed_definition,
        aliases=prop.proposed_aliases,
        category=prop.proposed_category,
        context=prop.proposed_context,
        source=term_source,
    )


# --- read helpers ------------------------------------------------------------


async def get_job(session: AsyncSession, *, org_id: int, job_id: int) -> LabGlossaryScanJob | None:
    return (
        await session.execute(
            select(LabGlossaryScanJob).where(
                LabGlossaryScanJob.id == job_id, LabGlossaryScanJob.organization_id == org_id
            )
        )
    ).scalar_one_or_none()


async def list_proposals(session: AsyncSession, *, job_id: int) -> list[LabGlossaryScanProposal]:
    return list(
        (
            await session.execute(
                select(LabGlossaryScanProposal)
                .where(LabGlossaryScanProposal.scan_job_id == job_id)
                .order_by(LabGlossaryScanProposal.proposal_type.asc(), LabGlossaryScanProposal.term.asc())
            )
        )
        .scalars()
        .all()
    )


async def pending_review_count(session: AsyncSession, *, org_id: int) -> int:
    """Number of proposals across the org still awaiting review."""
    return int(
        await session.scalar(
            select(func.count())
            .select_from(LabGlossaryScanProposal)
            .join(LabGlossaryScanJob, LabGlossaryScanJob.id == LabGlossaryScanProposal.scan_job_id)
            .where(
                LabGlossaryScanJob.organization_id == org_id,
                LabGlossaryScanProposal.review_status == "pending",
            )
        )
        or 0
    )


async def pending_review_job_ids(session: AsyncSession, *, org_id: int) -> list[int]:
    """Scan/import job ids that still have proposals awaiting review, most recent
    first. The pending banner uses these to open the review flow."""
    rows = (
        (
            await session.execute(
                select(LabGlossaryScanProposal.scan_job_id)
                .join(LabGlossaryScanJob, LabGlossaryScanJob.id == LabGlossaryScanProposal.scan_job_id)
                .where(
                    LabGlossaryScanJob.organization_id == org_id,
                    LabGlossaryScanProposal.review_status == "pending",
                )
                .group_by(LabGlossaryScanProposal.scan_job_id)
                .order_by(LabGlossaryScanProposal.scan_job_id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [int(r) for r in rows]
