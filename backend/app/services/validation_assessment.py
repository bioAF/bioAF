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
from app.services.validation_issue_service import ValidationIssueService

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

    change_7.3 section 1: an artifact the bundle turned out not to contain is settled for that
    bundle; only one never attempted, or whose retrieval failed, is worth another fetch.
    """
    from app.services.supplement_inventory import RETRIEVAL_FAILED, RETRIEVAL_NOT_ATTEMPTED, establish_identity

    supplements = [s for s in (evidence or {}).get("supplements") or [] if isinstance(s, dict)]
    return any(
        not s.get("resolved") and s["retrieval"]["status"] in (RETRIEVAL_NOT_ATTEMPTED, RETRIEVAL_FAILED)
        for s in establish_identity(supplements)
    )


RETRIEVAL_STEP = "retrieving the paper's supplementary files"


def retrieval_issue(entries: list[dict], supplements: list[dict]) -> dict | None:
    """One issue for a retrieval source whose attempts all failed, naming what it affected.

    change_7.3 section 9: one per failed SOURCE, never one per artifact. Study 34 showed one bundle
    failure about sixteen times. The sentence is plain; the URL, status, error class, attempts and
    times travel as technical detail.
    """
    if not entries or entries[-1].get("outcome") == "retrieved":
        return None
    last = entries[-1]
    affected = [
        str(s.get("label"))
        for s in supplements
        if isinstance(s, dict)
        and (s.get("retrieval") or {}).get("ledger") == last.get("id")
        and s.get("kind") in ("attachment", "reference")
    ]
    message = "bioAF could not download the paper's supplementary files in this attempt"
    message += f", so these were not inspected: {', '.join(affected)}." if affected else "."
    return {
        "step": RETRIEVAL_STEP,
        "outcome": "retrieval_failed",
        "impact": "degraded",
        "message": message,
        "model": None,
        "technical_detail": {
            "source": last.get("source_label"),
            "url": last.get("url"),
            "http_status": last.get("http_status"),
            "error_class": last.get("error_class"),
            "outcome": last.get("outcome"),
            "attempts": len(entries),
            "first_at": entries[0].get("at"),
            "last_at": last.get("at"),
            "ledger": [e.get("id") for e in entries],
        },
    }


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
        (
            await session.execute(
                select(ReproductionPlan)
                .where(
                    ReproductionPlan.validation_study_id == study.id,
                    ReproductionPlan.superseded_at.is_(None),
                )
                .order_by(ReproductionPlan.id.desc())
            )
        )
        .scalars()
        .first()
    )


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
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    wanted = {
        log2
        for t in rows
        if t.threshold is not None and (log2 := _as_log2_cutoff(t.threshold_kind, t.threshold)) is not None
    }
    # change_7.3 section 7: a claim's cutoffs are structure now, and a two-part cutoff carries its
    # fold change there rather than in the one scalar.
    for t in rows:
        for cutoff in t.cutoffs or []:
            if (
                isinstance(cutoff, dict)
                and (log2 := _as_log2_cutoff(cutoff.get("kind"), cutoff.get("value"))) is not None
            ):
                wanted.add(log2)
    return sorted(wanted)


def _as_log2_cutoff(kind, value) -> float | None:
    """A fold-change cutoff on the |log2FC| scale the table is measured on, or None for anything else.

    change_7.5 section 1.2: a linear fold change was read as log2, so "twofold" was measured as
    |log2FC| > 2, which is fourfold. A linear fold change of x is |log2FC| > log2(x).
    """
    import math

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    kind = str(kind or "").lower()
    if kind in ("abs_log2fc", "log2fc"):
        return number
    if kind == "fold_change" and number > 1:
        return math.log2(number)
    return None


async def resolve_study_supplements(session: AsyncSession, study, evidence: dict) -> list[dict]:
    """Download the paper's attachments and establish what each one is. Never raises.

    This is the point where the read-time manifest of NAMES becomes an inventory of FILES with
    roles. An article with no PMC id has nothing to download, which is a limitation of the run and
    leaves the references exactly as they were.

    change_7.3 section 1: every attempt lands in ``evidence["retrieval_ledger"]``, which this
    updates in place, and one failed source becomes one issue.
    """
    from app.services.supplement_inventory import merge_resource_identity, recorded_failures, resolve_supplements

    references = evidence.get("supplements") or []
    pmcid = (evidence.get("pmcid") or "").strip()
    if not pmcid:
        return references
    ledger = list(evidence.get("retrieval_ledger") or [])
    if not ledger:
        # A study recorded before the ledger carries its failure only as a copy on each row, and the
        # attempt below clears those copies. The failure is written into the ledger first, so a
        # resumed study shows the original failure beside the new attempt.
        ledger.extend(recorded_failures(references, pmcid=pmcid, at=(evidence.get("assessment") or {}).get("at")))
    start = len(ledger)
    try:
        resolved = await resolve_supplements(
            pmcid,
            references,
            fetcher=deposit_bytes_fetcher,
            thresholds=await claimed_thresholds(session, study),
            ledger=ledger,
        )
    except Exception as exc:  # noqa: BLE001 - an inventory failure degrades the report, never fails the study
        logger.warning("supplement resolution failed for study %s: %s", study.id, exc)
        return references
    evidence["retrieval_ledger"] = ledger
    await ValidationIssueService.record(session, study, [retrieval_issue(ledger[start:], resolved)])
    # One file is one resource. The prose reference and the manifest entry resolve to the same
    # bytes, and study 32 listed each of S1, S2 and S3 twice as a result.
    return merge_resource_identity(resolved)


async def reconcile_plan(session: AsyncSession, study, supplements: list[dict]) -> None:
    """Re-interpret the plan against what the supplements turned out to hold. Never raises."""
    from app.services.validation_reconciliation import not_performed, reconcile

    plan = await active_plan(session, study)
    if plan is None:
        return not_performed("this study has no reproduction plan to reconcile")
    cfg = await llm_provider_config_service.get_active(session, study.organization_id)
    if cfg is None:
        logger.info("study %s: no active provider, so nothing can reconcile the plan", study.id)
        return not_performed(
            "no language model is configured for this organization, so nothing could reconcile the plan"
        )
    # change_7.3 section 9: this caller dropped `on_issue`, so a model failing inside reconciliation
    # reached nothing but a log line.
    issues: list[dict] = []
    try:
        result = await reconcile(
            session,
            study,
            plan,
            supplements=supplements,
            client=get_client(cfg.provider),
            model=cfg.model,
            api_key=cfg.api_key,
            on_issue=issues.append,
        )
    except Exception as exc:  # noqa: BLE001 - a reconciliation failure degrades the report only
        logger.warning("reconciliation failed for study %s: %s", study.id, exc)
        result = not_performed("bioAF hit an internal error while reconciling the plan", error_class=type(exc).__name__)
    await ValidationIssueService.record(session, study, issues)
    result["model_issue"] = any(issues)
    return result


RECONCILIATION_STEP = "reconciling the plan against the paper's evidence"
CONSISTENCY_STEP = "checking this assessment's statements for contradictions"


def _not_performed_issue(step: str, reason: str, **detail) -> dict:
    """change_7.3 section 9: a stage that could not run is an issue, stated plainly, with any
    technical detail beside the sentence rather than in it."""
    return {
        "step": step,
        "outcome": "not_performed",
        "impact": "degraded",
        "message": f"This step was not performed: {reason}.",
        "model": None,
        "technical_detail": {k: v for k, v in detail.items() if v is not None} or None,
    }


def refresh_checks(study, plan) -> None:
    """Re-settle the read-time checks against what the assessment established. Never raises.

    change_7.3 section 6: the driver said reconciliation re-ran the pre-compute checks once
    supplements resolved, and nothing did. Study 34 kept a read-time "samples described: ok" whose
    reasoning cited supplementary metadata nobody had inspected. A verdict that rests on the paper's
    text stays marked as such until something inspected settles it.
    """
    from app.services.validation_precompute_checks import (
        BASIS_INSPECTED,
        CHECK_SAMPLE_DATA,
        CHECK_SAMPLES_DESCRIBED,
        OK,
        UNKNOWN,
        basis_of,
        check_sample_data,
    )

    evidence = dict(study.evidence_json or {})
    checks = {k: dict(v) if isinstance(v, dict) else v for k, v in (evidence.get("precompute_checks") or {}).items()}
    supplements = [s for s in evidence.get("supplements") or [] if isinstance(s, dict)]
    deposits = [d for d in ((evidence.get("capabilities") or {}).get("deposits") or []) if isinstance(d, dict)]

    table = next(
        (
            s
            for s in supplements
            if s.get("resolved") and s.get("role") == "sample_metadata" and (s.get("row_count") or 0) > 0
        ),
        None,
    )
    described = checks.get(CHECK_SAMPLES_DESCRIBED)
    if table is not None and isinstance(described, dict):
        columns = ", ".join(str(c) for c in (table.get("columns") or [])[:6])
        described.update(
            verdict=OK,
            detail=(
                f"{table.get('label')} was retrieved and lists {table.get('row_count')} samples"
                + (f" with {columns} for each" if columns else "")
                + ", so which sample belongs to which condition is recoverable from it"
            ),
            decided_by="measurement",
            basis=BASIS_INSPECTED,
        )

    sample_data = checks.get(CHECK_SAMPLE_DATA)
    paper_count = ((plan.sample_sheet_json if plan else None) or {}).get("sample_count")
    if isinstance(sample_data, dict) and sample_data.get("verdict") == UNKNOWN:
        rerun = check_sample_data(
            paper_sample_count=paper_count if isinstance(paper_count, int) else None,
            entries=[],
            supplements=supplements,
            deposits=deposits,
        )
        if rerun.get("verdict") != UNKNOWN or "no deposited files were listed" in str(sample_data.get("detail")):
            checks[CHECK_SAMPLE_DATA] = rerun

    for check in checks.values():
        if isinstance(check, dict) and not check.get("basis"):
            check["basis"] = basis_of(check)
    if checks:
        evidence["precompute_checks"] = checks
        study.evidence_json = evidence


async def run_assessment(session: AsyncSession, study) -> dict:
    """The public assessment stage. Never raises; always produces a record.

    Runs on every authorized study on both routes, after authorization and before the final
    execution-feasibility decision. It concludes nothing about the study's state: that is the
    caller's, and it differs between a route that can still run and one that cannot.

    change_7.3 section 6: **the record says what reconciliation and the contradiction pass covered,
    and whether they ran at all.** ``reconciled: false`` stood for five different causes, and an
    empty contradiction list read as consistency established when the pass had not run.
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

    plan = await active_plan(session, study)
    refresh_checks(study, plan)
    await session.flush()

    # change_7.1 section 6: the evidence is in hand, so the provisional reading gets revised before
    # anything is concluded from it. Retrieval on its own changed no decision.
    reconciliation = await reconcile_plan(session, study, (study.evidence_json or {}).get("supplements") or [])
    if reconciliation["status"] != "performed" and not reconciliation.get("model_issue"):
        await ValidationIssueService.record(
            session,
            study,
            [
                _not_performed_issue(
                    RECONCILIATION_STEP, reconciliation["reason"], error_class=reconciliation.get("error_class")
                )
            ],
        )

    # change_7.3 section 7: a count equal to the collected inventory and tagged post-QC is marked
    # unresolved. The deposit's registered count is established evidence reaching a consumer.
    await guard_population_counts(session, study, plan)

    # change_7.2 section 4: reconciliation settles its DEPENDENTS. Reaching this stage is not the
    # same as the report being consistent, and study 33 shipped a check asserting that per-sample
    # assignments are recoverable beside a blocker asserting they cannot be reconstructed.
    contradiction_pass = await settle_dependents(session, study)
    contradictions = contradiction_pass["findings"]

    evidence = dict(study.evidence_json or {})
    supplements = [
        s for s in evidence.get("supplements") or [] if isinstance(s, dict) and s.get("kind") not in ("figure", "index")
    ]
    resolved = [s for s in supplements if s.get("resolved")]
    performed = reconciliation["status"] == "performed"
    record = {
        "contradictions": contradictions,
        "unresolved_contradictions": [c for c in contradictions if c.get("status") == "unresolved"],
        "contradiction_pass": {
            "checked": contradiction_pass["checked"],
            "pairs": contradiction_pass["pairs"],
            "reason": contradiction_pass.get("reason"),
        },
        "at": _now_iso(),
        "supplements_inspected": len(resolved),
        "supplements_unresolved": len(supplements) - len(resolved),
        "reconciliation": {
            "status": reconciliation["status"],
            "reason": reconciliation.get("reason"),
            "basis": reconciliation.get("basis"),
        },
        # What every finding in this assessment rests on until something inspected settles it.
        "basis": reconciliation.get("basis") if performed else "paper_text",
        "reconciled": performed,
        "revisions": len(reconciliation.get("revisions") or []),
    }
    evidence["assessment"] = record
    study.evidence_json = evidence
    await session.flush()
    return record


async def guard_population_counts(session: AsyncSession, study, plan) -> list[int]:
    """Mark a post-QC count that equals a deposit's registered sample count as unresolved.

    change_7.3 section 7. Study 34 recorded "54 samples, post-QC" beside a paper that excludes three
    TE biopsies after quality control, leaving 51 analysed, and an EGA record that registers 54. The
    count is the collected inventory; the analysed population is a different number. The claim is
    never rewritten: the registered count is evidence, the paper's number is the paper's, and which
    population the sentence meant is exactly what is unresolved.

    Returns the ids of the targets it marked.
    """
    from app.models.comparison_target import ComparisonTarget

    if plan is None:
        return []
    evidence = study.evidence_json or {}
    registered = [
        (str(d.get("accession")), d.get("registered_samples"))
        for d in ((evidence.get("capabilities") or {}).get("deposits") or [])
        if isinstance(d, dict) and isinstance(d.get("registered_samples"), int)
    ]
    if not registered:
        return []
    excluded = bool(((evidence.get("paper_passages") or {}).get("statements") or []))
    targets = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    marked: list[int] = []
    for target in targets:
        stage = (target.qc_stage or "").lower().replace("-", "").replace(" ", "")
        # "as analysed" says the same thing as post-QC once the paper states that samples were
        # excluded: the deployed resume of study 34 had reconciliation relabel the 54 that way.
        # Without a stated exclusion an analysed count equal to the inventory is plausible, and left.
        analysed = "postqc" in stage or ("analys" in stage or "analyz" in stage) and excluded
        if not analysed or target.claimed_value is None or not _is_sample_count(target):
            continue
        match = next((acc for acc, count in registered if float(count) == float(target.claimed_value)), None)
        if match is None:
            continue
        target.unresolved_reason = (
            f"This count equals the {int(target.claimed_value)} samples {match} registers, which is the collected "
            f"inventory, but the claim describes it as {target.qc_stage}."
            + (
                " The paper states that samples were excluded after quality control, so fewer were analysed."
                if excluded
                else ""
            )
            + " Which population it counts is unresolved; the number is not rewritten."
        )
        marked.append(target.id)
    if marked:
        await session.flush()
    return marked


def _is_sample_count(target) -> bool:
    text = " ".join(str(v or "") for v in (target.metric_key, target.unit, target.claim_text)).lower()
    return (target.output_type or "count") == "count" and ("sample" in text or "biops" in text or "embryo" in text)


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


async def settle_dependents(session: AsyncSession, study) -> dict:
    """Express the assessment's dependent statements against the reconciled evidence.

    Returns ``{"checked", "pairs", "findings", "reason"}``: whether the pass ran, the pairs it
    compared, and every contradiction found, each either resolved by what was inspected or reported
    as unresolved. Never raises: a consistency pass that fails must not fail the study, and
    change_7.3 section 6 records its failure rather than returning ``[]``, which read as agreement.
    """
    from app.services import validation_consistency as consistency

    evidence = dict(study.evidence_json or {})
    plan = await active_plan(session, study)
    blockers = list((plan.blockers_json if plan else None) or [])

    try:
        findings = consistency.reconcile_contradictions(
            precompute_checks=evidence.get("precompute_checks"),
            blockers=blockers,
            supplements=evidence.get("supplements") or [],
            blocker_kinds=(plan.blocker_kinds_json if plan else None) or [],
        )
    except Exception as exc:  # noqa: BLE001 - a consistency failure degrades the report only
        logger.warning("consistency pass failed for study %s: %s", study.id, exc)
        reason = "bioAF hit an internal error while comparing this assessment's statements"
        await ValidationIssueService.record(
            session, study, [_not_performed_issue(CONSISTENCY_STEP, reason, error_class=type(exc).__name__)]
        )
        return {"checked": False, "pairs": [], "findings": [], "reason": reason}

    result = {"checked": True, "pairs": list(consistency.CHECKED_PAIRS), "findings": findings, "reason": None}
    if not findings:
        return result

    checks, remaining = consistency.apply_resolutions(
        precompute_checks=evidence.get("precompute_checks"), blockers=blockers, findings=findings
    )
    evidence["precompute_checks"] = checks
    study.evidence_json = evidence
    if plan is not None and remaining != blockers:
        plan.blockers_json = remaining
    await session.flush()
    return result


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
        # A paper read from a pasted body carries no manifest, so an empty inventory there means
        # nobody listed its attachments, not that it has none.
        manifest_known=bool(evidence.get("pmcid") or evidence.get("supplements")),
        # change_7.4 section 1.3: the acquisition record, so an acquired input is never reported as
        # none acquired.
        acquisition=evidence,
        data_run_id=study.data_run_id,
        fetched_samples=await _fetched_samples(session, study),
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


async def _fetched_samples(session: AsyncSession, study) -> int | None:
    """How many of the study's samples carry a fetched sequencing file, or None with no data run."""
    if not study.data_run_id or study.experiment_id is None:
        return None
    from sqlalchemy import func

    from app.models.sample import Sample, sample_files

    return int(
        (
            await session.execute(
                select(func.count(func.distinct(Sample.id)))
                .join(sample_files, Sample.id == sample_files.c.sample_id)
                .where(Sample.experiment_id == study.experiment_id)
            )
        ).scalar()
        or 0
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
