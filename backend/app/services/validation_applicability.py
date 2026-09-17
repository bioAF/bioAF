"""plan_8_2 section 4.1 and owner decision 4: applicability governs the whole report.

A paper whose experiments bioAF has no validation method for is not missing data. Study 46 (qRT-PCR,
western blot, AFM and imaging) was classified ``missing_data`` because no sequencing accession was
extracted, and its report followed a "Not applicable" scorecard with nf-core, reference and sequencing
failures. Applicability is decided per experiment and check, from what the read recorded:

- an experiment is within bioAF's methods when a workflow is mapped for its assay, or when one of its claims
  has a check bioAF can make (available or unresolved);
- a paper none of whose experiments or claims is within them is ``not_applicable``: "No eligible findings
  were identified for bioAF's current validation methods", which does not establish that the paper has no
  quantitative analysis;
- a paper with some experiments outside them is ``partial``: its supported findings are assessed as they
  are, and the rest are named as outside the assessment;
- methods too thin to name an assay, or no experiments recorded, leave applicability ``undetermined``.

Classification keeps the existing ``inconclusive`` (decision 4); no new value. The words are pending the
owner's sign-off.
"""

from __future__ import annotations

APPLICABLE = "applicable"
PARTIAL = "partial"
NOT_APPLICABLE = "not_applicable"
UNDETERMINED = "undetermined"

NO_ELIGIBLE = "No eligible findings were identified for bioAF's current validation methods."
NOT_ESTABLISHED = "This does not establish that the paper contains no quantitative analysis."
REQUIREMENT_DOES_NOT_APPLY = (
    "Does not apply: bioAF has no validation method for this paper's experiments, so this is not a requirement "
    "the paper fails."
)
OUTSIDE_METHODS = "outside_methods"
# The classifications an early exit gave a paper before applicability decided it (decision 4).
RESTATABLE = ("missing_data", "not_reproducible")

_ELIGIBLE = ("available", "unresolved")
_THIN_METHODS = "insufficient method detail"

# plan_8_3 section 1.3: what an experiment is supported BY, kept apart because the remedies differ.
SUPPORTED = "supported"
AWAITING_INPUT = "awaiting_input"
UNRESOLVED_INTERPRETATION = "unresolved_interpretation"
FAILED_DECISION = "failed_decision"
UNSUPPORTED = "unsupported"
SUPPORT_STATES = (SUPPORTED, AWAITING_INPUT, UNRESOLVED_INTERPRETATION, FAILED_DECISION, UNSUPPORTED)

# The unresolved requirements a RUN establishes. A claim waiting on one of these is a claim bioAF can
# check, once it has looked.
_RUN_ESTABLISHES = ("sample_mapping", "deposit_listing")
# The unresolved requirements that are bioAF's own failure to decide, never evidence of support. A
# binding that was cut off before it finished says nothing about whether the assay can be validated.
_FAILED_DECISIONS = ("binding",)

SUPPORT_REASONS = {
    SUPPORTED: "bioAF has a validation method for this experiment's assay",
    AWAITING_INPUT: "bioAF has a validation method for this experiment, and is waiting on what the run establishes",
    UNRESOLVED_INTERPRETATION: (
        "the only evidence for this experiment's workflow is a word describing its family, not the assay itself, "
        "so which measurement bioAF would analyze is not established"
    ),
    FAILED_DECISION: (
        "this experiment's checks are open because a decision bioAF asked a model for did not complete, which is "
        "bioAF's failure and does not establish that the assay can be validated"
    ),
    UNSUPPORTED: "bioAF has no validation method for this experiment's assay",
}


def _available(target: dict) -> bool:
    checks = target.get("checks") or {}
    return isinstance(checks, dict) and any(
        isinstance(c, dict) and c.get("status") == "available" for c in checks.values()
    )


def _open_requirements(target: dict) -> set[str]:
    checks = target.get("checks") or {}
    if not isinstance(checks, dict):
        return set()
    return {
        str(c.get("requirement"))
        for c in checks.values()
        if isinstance(c, dict) and c.get("status") == "unresolved" and c.get("requirement")
    }


def _eligible(target: dict) -> bool:
    """Whether a claim carries a check bioAF can make. A check left open by bioAF's own failed model
    decision is not one: it says nothing about the assay, and counting it promoted study 48's
    experiments to supported."""
    if _available(target):
        return True
    open_for = _open_requirements(target)
    return bool(open_for) and not open_for <= set(_FAILED_DECISIONS)


def _workflow_basis(experiment: dict) -> str | None:
    """What established this experiment's workflow: ``diagnostic`` when the paper's own assay names it,
    ``contextual`` when only a word describing its family does, None when it has none.

    plan_8_3 section 1.3: an assay is not routed into an RNA-seq workflow merely because its
    description includes RNA or gene expression. A contextual match is the family's answer of last
    resort, and by itself it is an unresolved interpretation rather than an established contract.
    """
    from app.services.pipeline_mapper import match_route

    if not experiment.get("workflow"):
        return None
    if str(experiment.get("library_strategy") or "").strip():
        # The deposit itself declares what the data is, which settles the measurement type.
        return "diagnostic"
    matched = match_route(str(experiment.get("assay") or "").lower())
    if matched is None:
        return None
    return "diagnostic" if matched[1] else "contextual"


def support_for(experiment: dict, claims: list[dict]) -> str:
    """What this experiment is supported by, from what the read actually established."""
    if any(_available(t) for t in claims):
        return SUPPORTED
    basis = _workflow_basis(experiment)
    open_for = {r for t in claims for r in _open_requirements(t)}
    # A failed decision first: it is bioAF's own, and it is the one state that must never read as
    # support however good the workflow looks.
    if open_for and open_for <= set(_FAILED_DECISIONS):
        return FAILED_DECISION
    if open_for & set(_RUN_ESTABLISHES):
        return AWAITING_INPUT if basis else UNSUPPORTED
    if basis == "diagnostic":
        return SUPPORTED
    if basis == "contextual":
        return UNRESOLVED_INTERPRETATION
    return UNSUPPORTED


def _limitation(rows: list[dict]) -> str | None:
    """The limitation sentence, naming each unsupported assay once with its experiments."""
    by_assay: dict[str, list[str]] = {}
    for row in rows:
        by_assay.setdefault(row["assay"] or "an assay the paper does not name", []).append(str(row["id"]))
    if not by_assay:
        return None
    parts = [
        f"{assay} ({'experiment' if len(ids) == 1 else 'experiments'} {', '.join(ids)})"
        for assay, ids in by_assay.items()
    ]
    joined = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} or {parts[-1]}"
    return f"bioAF has no validation method for {joined}."


def applicability(plan: dict | None, targets: list[dict] | None) -> dict | None:
    """``{"status", "statement", "limitation", "experiments", "eligible_claims"}`` for a read plan, or None
    when there is no plan."""
    if not plan:
        return None
    targets = [t for t in targets or [] if isinstance(t, dict)]
    experiments = [e for e in plan.get("reported_experiments") or [] if isinstance(e, dict)]
    eligible_total = sum(1 for t in targets if _eligible(t))
    rows = []
    for experiment in experiments:
        claims = [t for t in targets if t.get("reported_experiment_id") == experiment.get("id")]
        eligible = sum(1 for t in claims if _eligible(t))
        # plan_8_3 section 1.3: support comes from implemented capabilities, never from a workflow
        # name alone and never from a check a failed model decision left open.
        support = support_for(experiment, claims)
        rows.append(
            {
                "id": experiment.get("id"),
                "assay": experiment.get("assay"),
                "workflow": experiment.get("workflow"),
                "claims": len(claims),
                "eligible_claims": eligible,
                "support": support,
                "support_reason": SUPPORT_REASONS[support],
                "supported": support in (SUPPORTED, AWAITING_INPUT),
            }
        )
    unsupported = [r for r in rows if not r["supported"]]
    thin = any(_THIN_METHODS in str(b).lower() for b in plan.get("blockers") or [])
    if eligible_total == 0 and (thin or not rows):
        status = UNDETERMINED
    elif rows and not any(r["supported"] for r in rows):
        status = NOT_APPLICABLE
    elif unsupported:
        status = PARTIAL
    else:
        status = APPLICABLE
    return {
        "status": status,
        "statement": f"{NO_ELIGIBLE} {NOT_ESTABLISHED}" if status == NOT_APPLICABLE else None,
        "limitation": _limitation(unsupported) if status in (NOT_APPLICABLE, PARTIAL) else None,
        "experiments": rows,
        "eligible_claims": eligible_total,
    }


def restatement(study: dict, found: dict | None) -> dict | None:
    """The classification an early exit gave a paper outside bioAF's methods, and what it is restated as on
    request (decision 4), or None."""
    if (
        study.get("state") == "classified"
        and study.get("classification") in RESTATABLE
        and (found or {}).get("status") == NOT_APPLICABLE
    ):
        return {"from": study.get("classification"), "to": "inconclusive"}
    return None


def outcome_reason(found: dict) -> str:
    """The failure reason a paper outside bioAF's methods is classified with."""
    return " ".join(p for p in (found.get("statement"), found.get("limitation")) if p)
