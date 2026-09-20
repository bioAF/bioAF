"""plan_8_5 section 3.2: one assessment, produced where the evidence is, persisted, and read back.

The v3 card used to be derived twice on every page load: once for the report and once for the list,
each from whatever the caller happened to pass. Nothing read the snapshot the study stored. Two
surfaces could disagree, a refresh could quietly rescore old evidence, and an outcome that costs a
model call could not exist at all, because rubric v3 forbids a page calling a model.

So the production path settles the obligations and stores them, and everything else projects what
was stored:

- **each obligation is its own record**, under its leaf id, with its outcome, rationale, scope,
  method and (for a failure) its impact. An aggregate is not a per-obligation contract;
- **the allocation travels with the outcomes**, so a card rendered from the record shows the weights
  it was scored under, and a later scope change is a new revision rather than a silent rescore;
- **reuse is keyed on what the checks actually read**, with the checker's own version beside it. A
  changed checker never reuses the old checker's accepted outcome, and evidence no check reads
  (a retrieval ledger's timestamps) never invalidates one.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from fractions import Fraction

# Bump when a check's meaning changes, so accepted outcomes from the previous one are not reused.
# plan_8_5 section 3.2: a changed checker must not reuse the old checker's accepted outcome.
CHECKER_VERSION = 1

# What the checks actually read. Evidence outside this cannot change an outcome, so it cannot
# invalidate one either: a re-fetched artifact must not cost every study a fresh set of judgments.
EVIDENCE_KEYS = ("precompute_checks", "sample_records", "input_choice", "code_inspection", "methods_cutoffs")
PLAN_KEYS = ("reported_experiments", "differential_design", "sample_sheet", "finding_inventory")
CLAIM_KEYS = ("index", "consistency", "predicate_detail")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def assessment_inputs(*, plan: dict | None, evidence: dict | None, claims, inventory: dict | None) -> dict:
    """What this assessment rests on, as hashes: the evidence, the plan, the claims, the inventory."""
    plan = plan or {}
    evidence = evidence or {}
    return {
        "evidence": _digest({key: evidence.get(key) for key in EVIDENCE_KEYS}),
        "plan": _digest({key: plan.get(key) for key in PLAN_KEYS}),
        "claims": _digest(
            [{key: claim.get(key) for key in CLAIM_KEYS} for claim in claims or [] if isinstance(claim, dict)]
        ),
        "inventory_revision": (inventory or {}).get("revision"),
        "checker_version": CHECKER_VERSION,
    }


def reusable(held: dict | None, inputs: dict) -> bool:
    """Whether a held assessment still answers for these inputs (plan_8_5 section 3.2)."""
    if not isinstance(held, dict) or not isinstance(held.get("inputs"), dict):
        return False
    if held.get("checker_version") != CHECKER_VERSION:
        return False
    return held["inputs"] == inputs


def _serialize_profile(profile: dict) -> dict:
    return {
        "rubric_version": profile.get("rubric_version"),
        "revision": profile.get("revision"),
        "sections": {key: str(value) for key, value in (profile.get("sections") or {}).items()},
        "weights": {key: str(value) for key, value in (profile.get("weights") or {}).items()},
        "exclusions": list(profile.get("exclusions") or []),
        "ceilings": {key: str(value) for key, value in (profile.get("ceilings") or {}).items()},
    }


def _deserialize_profile(stored: dict) -> dict:
    return {
        "rubric_version": stored.get("rubric_version"),
        "revision": stored.get("revision"),
        "sections": {key: Fraction(value) for key, value in (stored.get("sections") or {}).items()},
        "weights": {key: Fraction(value) for key, value in (stored.get("weights") or {}).items()},
        "exclusions": list(stored.get("exclusions") or []),
        "ceilings": {key: Fraction(value) for key, value in (stored.get("ceilings") or {}).items()},
    }


def _serialize_leaves(leaves: list[dict]) -> list[dict]:
    return [{**leaf, "weight": str(leaf["weight"])} for leaf in leaves]


def _deserialize_leaves(stored: list[dict]) -> list[dict]:
    return [{**leaf, "weight": Fraction(leaf["weight"])} for leaf in stored or []]


def build_assessment(
    *,
    plan: dict | None,
    evidence: dict | None,
    claims,
    inventory: dict | None,
    revision: int = 1,
) -> dict:
    """Settle every obligation this build can settle from the study's held evidence, as one record."""
    from app.services.validation_rubric_evidence import assess_evidence, profile_for
    from app.services.validation_rubric_v3 import RUBRIC_VERSION, allocate, result_allocation

    plan = plan or {}
    evidence = evidence or {}
    claims = [c for c in claims or [] if isinstance(c, dict)]
    workflows = [
        e.get("workflow") for e in plan.get("reported_experiments") or [] if isinstance(e, dict) and e.get("workflow")
    ]
    profile = profile_for(plan=plan)
    leaves = allocate(profile, results=result_allocation(inventory, workflows=workflows))
    return {
        "rubric_version": RUBRIC_VERSION,
        "checker_version": CHECKER_VERSION,
        "revision": revision,
        "at": _now_iso(),
        "profile": _serialize_profile(profile),
        "leaves": _serialize_leaves(leaves),
        "outcomes": assess_evidence(plan=plan, evidence=evidence, claims=claims, inventory=inventory),
        "inputs": assessment_inputs(plan=plan, evidence=evidence, claims=claims, inventory=inventory),
    }


def card_from(assessment: dict, *, reproduction: dict | None = None) -> dict:
    """The v3 card, projected from a stored assessment. It judges nothing and reads nothing else."""
    from app.services.validation_rubric_evidence import CAPABILITY_LIMITS
    from app.services.validation_rubric_v3 import evidence_card

    card = evidence_card(
        profile=_deserialize_profile(assessment.get("profile") or {}),
        leaves=_deserialize_leaves(assessment.get("leaves")),
        assessed=assessment.get("outcomes") or {},
        reproduction=reproduction,
        capability_limits=CAPABILITY_LIMITS,
    )
    # Which stored revision this card is, so a reader and an export can say what they are showing.
    card["assessment_revision"] = assessment.get("revision")
    card["assessed_at"] = assessment.get("at")
    card["checker_version"] = assessment.get("checker_version")
    return card
