"""change_7.2 section 4: the public assessment, as a stage both entrances can run.

Everything change_7.1 built (supplement resolution, retrieval propagation, reconciliation,
evidence-based completion) was attached to `_finish_without_execution`, which only a refused route
ever reached. The first study approved by hand walked past all of it, and the improvements were
therefore delivered to exactly one path.

This module is that work, lifted out of the driver so the gate can call it too. It needs no pipeline
execution and no controlled-data credentials: a missing deposit says nothing about whether the
journal published a usable attachment.

**Assessment completion is not execution completion.** The stage always produces an assessment
record. Where execution is possible, a terminal classification follows execution; where it is not,
the assessment record and its stated limitation ARE the terminal outcome. A study that can still run
must not be classified by this stage.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_provider_config_service
from app.services.llm_provider_clients import get_client
from app.services.validation_completion import completion_for

logger = logging.getLogger("bioaf.validation_assessment")


async def deposit_bytes_fetcher(url: str) -> bytes:
    """Default byte fetcher for a deposited file. Bytes, not text: the format is decided from magic
    bytes and a text decode would destroy a spreadsheet before it could be recognised."""
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


def propagate_retrieval(capabilities: dict, supplements: list[dict]) -> dict:
    """Carry retrieval outcomes from the inventory onto the capability answers.

    change_7.1 section 7: a resource cannot be recorded as retrieved and as accessibility-not-
    attempted at the same time, and a consumer that never hears about the retrieval keeps
    reporting the stale answer beside the new fact.
    """
    from app.services.supplement_inventory import apply_retrieval_to_code_sources

    updated = dict(capabilities)
    updated["code_sources"] = apply_retrieval_to_code_sources(capabilities.get("code_sources") or [], supplements)
    return updated


def independent_checks_outstanding(evidence: dict) -> bool:
    """Whether anything can still be established without compute or credentials.

    change_7.1 section 4: a blocked execution route does not end the assessment. Reading the sample
    metadata, inspecting the code the authors supplied and checking their results table need no
    cluster and no data access agreement, and the assessment is what merges, not the reproduction.
    """
    supplements = (evidence or {}).get("supplements") or []
    return any(not s.get("resolved") for s in supplements if isinstance(s, dict))


async def active_plan(session: AsyncSession, study):
    """The study's one active reproduction plan.

    change_7.2 section 2: every recent study carried two plans, both back-linked, and a query on the
    back-link read targets from both of them. The forward pointer names the active one; the
    superseded flag is what makes a back-link query safe when it is missing.
    """
    from app.models.reproduction_plan import ReproductionPlan

    if study.reproduction_plan_id:
        plan = (
            await session.execute(select(ReproductionPlan).where(ReproductionPlan.id == study.reproduction_plan_id))
        ).scalar_one_or_none()
        if plan is not None:
            return plan
    return (
        await session.execute(
            select(ReproductionPlan)
            .where(
                ReproductionPlan.validation_study_id == study.id,
                ReproductionPlan.superseded_at.is_(None),
            )
            .order_by(ReproductionPlan.id.desc())
        )
    ).scalars().first()


async def claimed_thresholds(session: AsyncSession, study) -> list[float]:
    """The fold-change cutoffs this paper's claims actually name.

    change_7.1 section 7: measuring a results table at fixed cutoffs of 1 and 2 is Groff's rule in
    production code. A paper claiming a 1.5-fold cutoff needs 1.5 measured.

    change_7.2 section 2: read from the study's ACTIVE plan, so the query cannot depend on a cleanup
    having happened. Study 33 carried plans 33 and 34 and this read the cutoffs off both.
    """
    from app.models.comparison_target import ComparisonTarget

    plan = await active_plan(session, study)
    if plan is None:
        return []
    rows = (
        (
            await session.execute(
                select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)
            )
        )
        .scalars()
        .all()
    )
    wanted = {
        float(t.threshold)
        for t in rows
        if t.threshold is not None and (t.threshold_kind or "").lower() in ("abs_log2fc", "log2fc", "fold_change")
    }
    return sorted(wanted)


async def resolve_study_supplements(session: AsyncSession, study, evidence: dict) -> list[dict]:
    """Download the paper's attachments and establish what each one is. Never raises.

    This is the point where the read-time manifest of NAMES becomes an inventory of FILES with
    roles. An article with no PMC id has nothing to download, which is a limitation of the run and
    leaves the references exactly as they were.
    """
    from app.services.supplement_inventory import merge_resource_identity, resolve_supplements

    references = evidence.get("supplements") or []
    pmcid = (evidence.get("pmcid") or "").strip()
    if not pmcid:
        return references
    try:
        resolved = await resolve_supplements(
            pmcid,
            references,
            fetcher=deposit_bytes_fetcher,
            thresholds=await claimed_thresholds(session, study),
        )
    except Exception as exc:  # noqa: BLE001 - an inventory failure degrades the report, never fails the study
        logger.warning("supplement resolution failed for study %s: %s", study.id, exc)
        return references
    # One file is one resource. The prose reference and the manifest entry resolve to the same
    # bytes, and study 32 listed each of S1, S2 and S3 twice as a result.
    return merge_resource_identity(resolved)


async def reconcile_plan(session: AsyncSession, study, supplements: list[dict]) -> None:
    """Re-interpret the plan against what the supplements turned out to hold. Never raises."""
    from app.services.validation_reconciliation import reconcile

    plan = await active_plan(session, study)
    if plan is None:
        return
    cfg = await llm_provider_config_service.get_active(session, study.organization_id)
    if cfg is None:
        logger.info("study %s: no active provider, so nothing can reconcile the plan", study.id)
        return
    try:
        await reconcile(
            session,
            study,
            plan,
            supplements=supplements,
            client=get_client(cfg.provider),
            model=cfg.model,
            api_key=cfg.api_key,
        )
    except Exception as exc:  # noqa: BLE001 - a reconciliation failure degrades the report only
        logger.warning("reconciliation failed for study %s: %s", study.id, exc)


async def run_assessment(session: AsyncSession, study) -> dict:
    """The public assessment stage. Never raises; always produces a record.

    Runs on every authorized study on both routes, after authorization and before the final
    execution-feasibility decision. It concludes nothing about the study's state: that is the
    caller's, and it differs between a route that can still run and one that cannot.
    """
    from app.services.validation_provenance import record_stage

    record_stage(study, "assessment")
    evidence = dict(study.evidence_json or {})
    if independent_checks_outstanding(evidence):
        evidence["supplements"] = await resolve_study_supplements(session, study, evidence)
        # What retrieval established has to reach the rows that answer for it. Study 32 recorded
        # Supplemental File S2 as "not attempted" in the same bundle that had downloaded and read it.
        evidence["capabilities"] = propagate_retrieval(evidence.get("capabilities") or {}, evidence["supplements"])
        study.evidence_json = evidence
        await session.flush()

    # change_7.1 section 6: the evidence is in hand, so the provisional reading gets revised before
    # anything is concluded from it. Retrieval on its own changed no decision.
    await reconcile_plan(session, study, evidence.get("supplements") or [])

    # change_7.2 section 4: reconciliation settles its DEPENDENTS. Reaching this stage is not the
    # same as the report being consistent, and study 33 shipped a check asserting that per-sample
    # assignments are recoverable beside a blocker asserting they cannot be reconstructed.
    contradictions = await settle_dependents(session, study)

    evidence = dict(study.evidence_json or {})
    supplements = evidence.get("supplements") or []
    resolved = [s for s in supplements if isinstance(s, dict) and s.get("resolved")]
    record = {
        "contradictions": contradictions,
        "unresolved_contradictions": [c for c in contradictions if c.get("status") == "unresolved"],
        "at": _now_iso(),
        "supplements_inspected": len(resolved),
        "supplements_unresolved": len(supplements) - len(resolved),
        "reconciled": bool((evidence.get("reconciliation") or {}).get("fingerprint")),
        "revisions": len((evidence.get("reconciliation") or {}).get("revisions") or []),
    }
    evidence["assessment"] = record
    study.evidence_json = evidence
    await session.flush()
    return record


def record_refusal(study, route: str, decision, reason: str | None = None) -> dict:
    """Land a refused route on the study, identically from either entrance.

    Both the notice a reader sees and the typed action a report names come from here, so a study's
    evidence cannot depend on which door it came through.
    """
    evidence = dict(study.evidence_json or {})
    evidence["route_decision"] = decision.as_record()
    evidence["route_blocked"] = {
        "chosen": route,
        "reason": reason or decision.reason,
        "action": decision.action,
        "at": _now_iso(),
    }
    study.evidence_json = evidence
    return evidence


async def settle_dependents(session: AsyncSession, study) -> list[dict]:
    """Express the assessment's dependent statements against the reconciled evidence.

    Returns every contradiction found, each either resolved by what was inspected or reported as
    unresolved. Never raises: a consistency pass that fails must not fail the study, but its silence
    is recorded rather than mistaken for agreement.
    """
    from app.services.validation_consistency import apply_resolutions, reconcile_contradictions

    evidence = dict(study.evidence_json or {})
    plan = await active_plan(session, study)
    blockers = list((plan.blockers_json if plan else None) or [])

    try:
        findings = reconcile_contradictions(
            precompute_checks=evidence.get("precompute_checks"),
            blockers=blockers,
            supplements=evidence.get("supplements") or [],
        )
    except Exception as exc:  # noqa: BLE001 - a consistency failure degrades the report only
        logger.warning("consistency pass failed for study %s: %s", study.id, exc)
        return []

    if not findings:
        return []

    checks, remaining = apply_resolutions(
        precompute_checks=evidence.get("precompute_checks"), blockers=blockers, findings=findings
    )
    evidence["precompute_checks"] = checks
    study.evidence_json = evidence
    if plan is not None and remaining != blockers:
        plan.blockers_json = remaining
    await session.flush()
    return findings


async def conclude_without_execution(
    session: AsyncSession, study, reason: str, *, limitation: dict | None = None
) -> None:
    """Assess everything that needs no compute, then state the outcome. Never raises.

    The merge requirement is an accurate, completed assessment, not a successful reproduction.
    Reading the authors' sample metadata, inspecting the code they published and checking their
    results table are all public work, and none of it needs the data bioAF cannot obtain.
    """
    from app.services.validation_study_service import ValidationStudyService

    await run_assessment(session, study)

    evidence = dict(study.evidence_json or {})
    outcome = completion_for(
        route=study.intended_route or (evidence.get("route") or "deposit"),
        capabilities=evidence.get("capabilities") or {},
        supplements=evidence.get("supplements") or [],
        extra_limitations=[limitation] if limitation else None,
    )
    # The assessment reason is a dependent too: an unresolved contradiction has to reach the reader
    # of the outcome, not only the reader of the evidence bundle.
    unresolved = (evidence.get("assessment") or {}).get("unresolved_contradictions") or []
    if unresolved:
        outcome["limitations"] = [
            *outcome.get("limitations", []),
            {
                "kind": "failed_discovery",
                "resource": "this study's own statements",
                "operation": study.intended_route or "deposit",
                "detail": unresolved[0].get("outcome") or "two statements in this assessment disagree",
            },
        ]
        outcome["reason"] = " ".join(limitation["detail"] for limitation in outcome["limitations"])

    evidence["completion"] = outcome
    study.evidence_json = evidence
    study.failure_reason = outcome["reason"] or reason
    await session.flush()

    await ValidationStudyService.transition(
        session,
        study.id,
        study.organization_id,
        study.requested_by_user_id,
        "classified",
        classification=outcome["classification"],
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
