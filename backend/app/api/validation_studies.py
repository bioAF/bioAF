"""HTTP API for the literature-validation flow (lit_validation).

Thin glue over the services: request a study, run read-and-plan (B1 text -> B2/B3 extraction ->
plan_ready or an early-exit classification), then the C1 gate (approve/decline). RBAC via the
``lit_validation`` permission. The comparison/execution back half is not wired here yet.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_beta_feature, require_permission
from app.api.provenance_reports import ReportFormat
from app.database import get_session
from app.models.literature import LiteraturePaper
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.validation_study import ValidationStudy, classification_confidence
from app.schemas.validation_study import (
    ClassifyRequest,
    ComparisonTargetResponse,
    DeclineRequest,
    DifferentialDesignRequest,
    FindingSetRequest,
    ReadRequest,
    ReproductionPlanResponse,
    SampleManifestResponse,
    ValidationStudyRequest,
    ValidationStudyResponse,
    ValidationStudySummary,
)
from app.services.audit_service import log_action
from app.services.literature.accession_manifest_service import AccessionManifestService
from app.services.literature.ground_truth_fetch_service import GroundTruthFetchService
from app.services.provenance.report_service import ProvenanceReportService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_level3_service import supported_finding_kinds
from app.services.validation_study_service import ValidationStudyService

# lit_validation is a beta feature: when its flag is off, every endpoint here 404s (matching the hidden
# nav entry), so the feature is invisible to instances that have not opted in (spec-07).
router = APIRouter(
    prefix="/api/validation-studies",
    tags=["validation-studies"],
    dependencies=[require_beta_feature("lit_validation")],
)


async def _plan_response(session: AsyncSession, plan, org_id: int) -> ReproductionPlanResponse | None:
    if plan is None:
        return None
    return ReproductionPlanResponse(
        id=plan.id,
        accessions=plan.accessions_json,
        sample_sheet=plan.sample_sheet_json,
        pipeline_key=plan.pipeline_key,
        pipeline_version=plan.pipeline_version,
        parameters=plan.parameters_json,
        tools=plan.tools_json,
        differential_design=plan.differential_design_json,
        finding_claim=plan.finding_claim_json,
        reference_genome=plan.reference_genome,
        reference_build=plan.reference_build,
        mapping_confidence=plan.mapping_confidence,
        mapping_notes=plan.mapping_notes,
        blockers=plan.blockers_json,
        extractor_model=plan.extractor_model,
        extractor_provider=plan.extractor_provider,
        supported_finding_kinds=supported_finding_kinds(plan.pipeline_key),
        pipeline_installed=await _is_pipeline_installed(session, org_id, plan.pipeline_key),
        pipeline_registry_name=_registry_name(plan.pipeline_key),
        comparison_targets=[
            ComparisonTargetResponse(
                metric_key=t.metric_key,
                claimed_value=t.claimed_value,
                unit=t.unit,
                tolerance=t.tolerance,
                source_locator=t.source_locator,
            )
            for t in (plan.comparison_targets or [])
        ],
    )


def _registry_name(pipeline_key: str | None) -> str | None:
    """The bare nf-core name the install endpoint takes: ``nf-core/ampliseq`` -> ``ampliseq``."""
    if not pipeline_key or not pipeline_key.startswith("nf-core/"):
        return None
    return pipeline_key.split("/", 1)[1] or None


async def _is_pipeline_installed(session: AsyncSession, org_id: int, pipeline_key: str | None) -> bool | None:
    """Whether this org's catalog holds ``pipeline_key`` and has it enabled.

    Checked here, at read time, because a plan can be written long before it is approved and a
    pipeline can be installed in between. Nothing checked it at all before, so an approved plan
    naming a pipeline the instance lacked spent a whole fetchngs download before ``launch_run``
    refused it.
    """
    if not pipeline_key:
        return None
    row = (
        await session.execute(
            select(PipelineCatalogEntry.id).where(
                PipelineCatalogEntry.organization_id == org_id,
                PipelineCatalogEntry.pipeline_key == pipeline_key,
                PipelineCatalogEntry.enabled.is_(True),
            )
        )
    ).first()
    return row is not None


def _study_title(study: ValidationStudy, paper_title: str | None) -> str:
    """A scientist-facing display title: the source paper's title, else the DOI, else the accession,
    else 'Study #{id}' (so a study is always named by what it reproduces, never a bare id)."""
    if paper_title:
        return paper_title
    if study.source_doi:
        return study.source_doi
    if study.source_accession:
        return study.source_accession
    return f"Study #{study.id}"


async def _paper_titles(session: AsyncSession, studies: list[ValidationStudy], org_id: int) -> dict[int, str]:
    """Batch-resolve paper titles for a set of studies in one query (avoids an N+1 in the list)."""
    ids = {s.paper_id for s in studies if s.paper_id}
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(LiteraturePaper.id, LiteraturePaper.title).where(
                LiteraturePaper.id.in_(ids),
                LiteraturePaper.organization_id == org_id,
            )
        )
    ).all()
    return {pid: title for pid, title in rows}


async def _study_response(session: AsyncSession, study: ValidationStudy, org_id: int) -> ValidationStudyResponse:
    plan = await ReproductionPlanService.get_plan(session, study.id, org_id)
    paper_title = (await _paper_titles(session, [study], org_id)).get(study.paper_id) if study.paper_id else None
    return ValidationStudyResponse(
        id=study.id,
        state=study.state,
        title=_study_title(study, paper_title),
        classification=study.classification,
        confidence=classification_confidence(study.classification),
        paper_id=study.paper_id,
        source_doi=study.source_doi,
        source_accession=study.source_accession,
        experiment_id=study.experiment_id,
        reproduction_plan_id=study.reproduction_plan_id,
        approved_by_user_id=study.approved_by_user_id,
        failure_reason=study.failure_reason,
        plan=await _plan_response(session, plan, org_id),
        evidence=study.evidence_json,
    )


async def _load(session: AsyncSession, study_id: int, org_id: int) -> ValidationStudy:
    study = (
        await session.execute(
            select(ValidationStudy).where(
                ValidationStudy.id == study_id,
                ValidationStudy.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if not study:
        raise HTTPException(404, "Validation study not found")
    return study


@router.post("", response_model=ValidationStudyResponse)
async def request_validation(
    data: ValidationStudyRequest,
    current_user: dict = require_permission("lit_validation", "request"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await ValidationStudyService.create_study(
        session,
        org_id,
        user_id,
        paper_id=data.paper_id,
        source_doi=data.source_doi,
        source_accession=data.source_accession,
    )
    await session.commit()
    return await _study_response(session, study, org_id)


@router.get("", response_model=list[ValidationStudySummary])
async def list_studies(
    current_user: dict = require_permission("lit_validation", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    studies = await ValidationStudyService.list_studies(session, org_id)
    titles = await _paper_titles(session, studies, org_id)
    return [
        ValidationStudySummary(
            id=s.id,
            state=s.state,
            title=_study_title(s, titles.get(s.paper_id)),
            classification=s.classification,
            confidence=classification_confidence(s.classification),
            paper_id=s.paper_id,
            source_doi=s.source_doi,
            source_accession=s.source_accession,
            experiment_id=s.experiment_id,
            created_at=s.created_at,
        )
        for s in studies
    ]


@router.get("/{study_id}", response_model=ValidationStudyResponse)
async def get_study(
    study_id: int,
    current_user: dict = require_permission("lit_validation", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    study = await _load(session, study_id, org_id)
    return await _study_response(session, study, org_id)


@router.get("/{study_id}/provenance/report")
async def study_provenance_report(
    study_id: int,
    format: ReportFormat = Query(ReportFormat.json),
    current_user: dict = require_permission("lit_validation", "view"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """F3 / A3: export the study + its evidence bundle (JSON/Markdown/PDF/CSV/all) via the shared
    provenance report service. The report renders the full paper -> plan -> experiment -> runs chain.
    A viewer who can see the study can export it (gated ``lit_validation:view``)."""
    org_id = int(current_user["org_id"])
    await _load(session, study_id, org_id)  # 404 if missing or another org's study
    result = await ProvenanceReportService.generate(
        session=session,
        entity_type="validation_study",
        entity_id=study_id,
        org_id=org_id,
        user_email=current_user.get("email", ""),
        format=format.value,
    )
    await log_action(
        session=session,
        user_id=int(current_user["sub"]),
        entity_type="validation_study",
        entity_id=study_id,
        action="provenance_report_generated",
        details={"format": format.value},
    )
    await session.commit()

    content = result.content
    if isinstance(content, str):
        content = content.encode("utf-8")
    headers: dict[str, str] = {}
    if format != ReportFormat.json:
        headers["Content-Disposition"] = f'attachment; filename="{result.filename}"'
    return Response(content=content, media_type=result.content_type, headers=headers)


@router.post("/{study_id}/read", response_model=ValidationStudyResponse)
async def read_and_plan(
    study_id: int,
    data: ReadRequest,
    current_user: dict = require_permission("lit_validation", "request"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await _load(session, study_id, org_id)
    study = await ValidationDriverService.read_and_plan(session, study, data.full_text, org_id, user_id)
    await session.commit()
    return await _study_response(session, study, org_id)


@router.put("/{study_id}/differential-design", response_model=ValidationStudyResponse)
async def edit_differential_design(
    study_id: int,
    data: DifferentialDesignRequest,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    """B2e edit (Level-3): ratify/correct the paper's differential design at the C1 gate (typically to
    fix the contrast's sample labels to the analysis matrix's column names) before Level-3 runs it."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    await ReproductionPlanService.set_differential_design(
        session, study_id, org_id, user_id, {"contrasts": data.contrasts, "thresholds": data.thresholds or {}}
    )
    study = await _load(session, study_id, org_id)
    await session.commit()
    return await _study_response(session, study, org_id)


@router.get("/{study_id}/sample-manifest", response_model=SampleManifestResponse)
async def sample_manifest(
    study_id: int,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    """Level-3 picker source: resolve the study's deposited accession(s) into a per-sample manifest
    (title + condition + accessions) so the scientist assigns samples to the test/reference arms by
    RECOGNITION, never by typing accession tokens. Approve-time action (gated ``lit_validation:approve``).

    Best-effort: a study with no accession, or a metadata fetch that fails, returns 200 with an
    ``unavailable_reason`` (never a 500) so the gate degrades to today's free-text sample entry."""
    org_id = int(current_user["org_id"])
    await _load(session, study_id, org_id)  # 404 if missing or another org's study
    plan = await ReproductionPlanService.get_plan(session, study_id, org_id)
    accessions = [a for a in ((plan.accessions_json if plan else None) or []) if isinstance(a, str) and a.strip()]
    if not accessions:
        return SampleManifestResponse(
            samples=[], unavailable_reason="This study has no deposited accession to list samples from."
        )

    # Union across the plan's accessions, de-duping an experiment that appears in more than one.
    samples: list[dict] = []
    seen: set[str] = set()
    reasons: list[str] = []
    for accession in accessions:
        result = await AccessionManifestService.fetch_manifest(accession)
        if result.unavailable_reason:
            reasons.append(result.unavailable_reason)
        for entry in result.samples:
            key = (
                entry.get("experiment_accession")
                or entry.get("run_accession")
                or entry.get("sample_accession")
                or entry.get("title")
            )
            if not key or key in seen:
                continue
            seen.add(key)
            samples.append(entry)

    if not samples:
        reason = "; ".join(dict.fromkeys(reasons)) or "No samples were found for this study's accessions."
        return SampleManifestResponse(samples=[], unavailable_reason=reason)
    return SampleManifestResponse(samples=samples)


@router.post("/{study_id}/override-samples", response_model=ValidationStudyResponse)
async def override_samples(
    study_id: int,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    """ "Run with the samples we have": advance a study held in ``samples_mismatch`` (a picked sample was
    not fetched) to ``setup``. The design was already rewritten to the fetched samples, so the reduced
    reproduction runs cleanly. Records who overrode; approve-gated."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await _load(session, study_id, org_id)
    if study.state != "samples_mismatch":
        raise HTTPException(400, "This study is not held on missing samples.")
    study.evidence_json = {
        **(study.evidence_json or {}),
        "samples_override": {"user_id": user_id, "at": datetime.now(timezone.utc).isoformat()},
    }
    study = await ValidationStudyService.transition(session, study_id, org_id, user_id, "setup")
    await log_action(
        session=session,
        user_id=user_id,
        entity_type="validation_study",
        entity_id=study_id,
        action="samples_override_approved",
    )
    await session.commit()
    return await _study_response(session, study, org_id)


@router.get("/{study_id}/finding-set/candidates")
async def finding_set_candidates(
    study_id: int,
    kind: str = Query("gene"),
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    """B4 auto-fetch ASSIST (Level-3): best-effort GEO-supplementary candidates for the paper's
    deposited result set, to pre-fill the C1 confirm. Empty when nothing is found (the common case,
    per spike-03) so the human supplies the table; this never auto-confirms a ground-truth set."""
    org_id = int(current_user["org_id"])
    plan = await ReproductionPlanService.get_plan(session, study_id, org_id)
    if plan is None:
        raise HTTPException(404, "No reproduction plan for this study.")
    candidates: list[dict] = []
    for accession in plan.accessions_json or []:
        candidates.extend(await GroundTruthFetchService.fetch_geo_candidates(accession, kind=kind))
    return {"candidates": candidates}


@router.post("/{study_id}/finding-set", response_model=ValidationStudyResponse)
async def confirm_finding_set(
    study_id: int,
    data: FindingSetRequest,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    """B4 (Level-3): confirm the paper's deposited ground-truth result set at the C1 gate. Normalizes
    the supplied DEG/DA table into a directional FindingSet and persists it on the plan so approval
    can run Level-3 concordance. The response carries the updated plan (with the parsed finding set)
    for the human to review before approving."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    await ReproductionPlanService.set_finding_claim(
        session,
        study_id,
        org_id,
        user_id,
        kind=data.kind,
        table_text=data.table_text,
        contrast=data.contrast,
        lfc_threshold=data.lfc_threshold,
        padj_threshold=data.padj_threshold,
        source_locator=data.source_locator,
    )
    study = await _load(session, study_id, org_id)
    await session.commit()
    return await _study_response(session, study, org_id)


@router.post("/{study_id}/approve", response_model=ValidationStudyResponse)
async def approve_plan(
    study_id: int,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await ValidationStudyService.approve_plan(session, study_id, org_id, user_id)
    await session.commit()
    return await _study_response(session, study, org_id)


@router.post("/{study_id}/classify", response_model=ValidationStudyResponse)
async def classify_study(
    study_id: int,
    data: ClassifyRequest,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    """Manual comparison gate (Phase 1): a human records the terminal classification from
    ``comparing`` after reading the computed-vs-claimed evidence."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await ValidationStudyService.classify_by_hand(session, study_id, org_id, user_id, data.classification)
    await session.commit()
    return await _study_response(session, study, org_id)


@router.post("/{study_id}/decline", response_model=ValidationStudyResponse)
async def decline_plan(
    study_id: int,
    data: DeclineRequest,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await ValidationStudyService.decline_plan(session, study_id, org_id, user_id, reason=data.reason)
    await session.commit()
    return await _study_response(session, study, org_id)
