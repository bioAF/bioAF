"""plan_8_2 section 2.2: each claim's capabilities, refreshed from the evidence they rest on.

A claim's four checks (``validation_checks.evaluate_checks``) were computed once, while the paper was read.
Groff's supplement was retrieved afterwards, so its claims kept saying "no result table is published for
this claim's experiment" beside the comparison the supplement produced. This recomputes them whenever
the evidence behind them changes: after the assessment retrieves supplements, after a table is bound or
rejected, after a check concludes, after a recovery.

- **Capability** says whether a check can be attempted, from the evidence and the table bindings: a
  table bound to the claim's contrast makes consistency available; every listed table rejected for it
  makes it unavailable, saying which contrast each reports; a table still a candidate leaves it
  unresolved. **Activity** (pending, running) and **outcome** (what the check established) stay on the
  check record: a completed unresolved attempt is not an unavailable resource.
- Every check carries its ``basis``: a fingerprint of the evidence it was computed from, the binding
  version, and the check record's revision and outcome revision.
"""

from __future__ import annotations

from app.services import validation_check_queue as queue
from app.services.validation_checks import AUTHOR_RESULTS, AVAILABLE, UNAVAILABLE, UNRESOLVED, evaluate_checks

# plan_8_2 labels, pending the owner's sign-off.
NO_TABLE_FOR_CONTRAST = "no published result table reports this claim's contrast"
TABLE_NOT_BOUND = "which table reports this claim's contrast is not established"


def _evidence_fingerprint(evidence: dict, plan) -> str:
    supplements = [
        (s.get("identity") or s.get("filename") or s.get("label"), s.get("role"), bool(s.get("resolved")))
        for s in (evidence or {}).get("supplements") or []
        if isinstance(s, dict)
    ]
    deposits = [
        (d.get("accession"), d.get("exists"), d.get("access"), d.get("raw_data"), list(d.get("result_tables") or []))
        for d in ((evidence or {}).get("capabilities") or {}).get("deposits") or []
        if isinstance(d, dict)
    ]
    return queue.fingerprint(
        {
            "supplements": supplements,
            "deposits": deposits,
            "confirmations": (evidence or {}).get("table_confirmations"),
            "experiments": plan.reported_experiments_json,
            "design": plan.differential_design_json,
        }
    )


def _overlay(checks: dict, record, supplements: list[dict]) -> dict:
    """The claim's consistency capability, from the binding its check record established."""
    from app.services import validation_table_binding as binding

    author = dict(checks.get(AUTHOR_RESULTS) or {})
    if record is None or author.get("requirement") == "predicate":
        return checks
    deps = record.dependencies_json or {}
    outcome = record.outcome_json or {}
    bound = outcome.get("binding") if binding.established(outcome.get("binding")) else None
    chosen = deps.get("table") or {}
    table = chosen.get("name")
    index = chosen.get("supplement_index")
    if table and chosen.get("source") == "supplement" and isinstance(index, int) and 0 <= index < len(supplements):
        # The name a reader finds the supplement by.
        table = supplements[index].get("label") or table
    summary = [b for b in deps.get("bindings") or [] if isinstance(b, dict)]
    if bound is not None or (deps.get("binding") or {}).get("status") == binding.ESTABLISHED:
        kinds = [e.get("kind") for e in (bound or {}).get("evidence") or [] if isinstance(e, dict)] or list(
            (deps.get("binding") or {}).get("evidence") or []
        )
        how = f", bound to its contrast by {', '.join(k for k in kinds if k)}" if kinds else ""
        author.update(status=AVAILABLE, reason=f"the authors published {table}{how}", requirement=None)
    elif table is None and summary and all(b.get("status") == binding.REJECTED for b in summary):
        why = "; ".join(f"{b.get('name')}: {b.get('reason')}" for b in summary)
        author.update(status=UNAVAILABLE, reason=f"{NO_TABLE_FOR_CONTRAST} ({why})", requirement="result_table")
    elif table is None and summary:
        author.update(status=UNRESOLVED, reason=f"{TABLE_NOT_BOUND}", requirement="binding")
    elif table is not None and isinstance(outcome.get("binding"), dict) and not bound:
        author.update(
            status=UNRESOLVED,
            reason=f"{TABLE_NOT_BOUND}: {outcome['binding'].get('reason') or 'its binding is a candidate'}",
            requirement="binding",
        )
    return {**checks, AUTHOR_RESULTS: author}


async def refresh_claim_capabilities(session, study, plan) -> int:
    """Recompute every claim's checks from the study's current evidence and check records. Returns how many
    claims changed. Records only what changed; never raises for one claim."""
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget
    from app.services.validation_report_summary import target_dict
    from app.services.validation_resource_identity import canonical_resources
    from app.services.validation_table_binding import BINDING_VERSION

    evidence = study.evidence_json or {}
    targets = list(
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
    experiments = {e.get("id"): e for e in plan.reported_experiments_json or [] if isinstance(e, dict)}
    deposits = [d for d in ((evidence.get("capabilities") or {}).get("deposits") or []) if isinstance(d, dict)]
    resources = canonical_resources(plan.resources_json or [], deposits=deposits)
    supplements = [s for s in evidence.get("supplements") or [] if isinstance(s, dict)]
    contrasts = [c for c in (plan.differential_design_json or {}).get("contrasts") or [] if isinstance(c, dict)]
    records = {
        r.comparison_target_id: r
        for r in await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
        if r.reproduction_plan_id == plan.id
    }
    fingerprint = _evidence_fingerprint(evidence, plan)
    changed = 0
    for target in targets:
        row = target_dict(target)
        index = row.get("contrast_index")
        checks = evaluate_checks(
            row,
            experiment=experiments.get(row.get("reported_experiment_id")),
            resources=resources,
            deposits=deposits,
            supplements=supplements,
            contrast=contrasts[index] if isinstance(index, int) and 0 <= index < len(contrasts) else None,
        )
        record = records.get(target.id)
        checks = _overlay(checks, record, supplements)
        basis = {
            "evidence": fingerprint,
            "binding_version": BINDING_VERSION,
            "check_revision": getattr(record, "revision", None),
            "outcome_revision": getattr(record, "outcome_revision", None),
        }
        checks = {key: {**value, "basis": basis} for key, value in checks.items()}
        if checks != (target.checks or {}):
            target.checks = checks
            changed += 1
    if changed:
        await session.flush()
    return changed
