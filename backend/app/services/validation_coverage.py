"""plan_8_3 stage 2: what an analysis could FINISH, worked out before it is chosen.

The selector ranked one (claim, check) pair at a time: an authors' table first, then no unresolved
mapping, then the paper's own order. Nothing in that asked whether the selected work could finish a
finding. Study 50's F6 needs claims 13 and 14 on one contrast and claim 15 on another, so a run
selected for claim 13 leaves F6 unassessed however well it goes, while two other contrasts of the
same paper each complete two whole findings from one statistical output.

**A candidate is an analysis, not a claim.** One statistical fit over one experiment's one contrast
supplies every claim whose comparison that fit can validly make. Grouping by (check, experiment,
contrast) is what makes "this run completes F1 and F4" a computable fact rather than a hope.

**Feasibility, then coverage, then cost.** A candidate whose sample mapping is unresolved is not
runnable, and calling it a ranked option would describe hypothetical work as established capability.
Ready candidates rank by the weight of what they would complete, then how many findings that is,
then cost, with a stable tie-breaker.

**Nothing here looks at a result.** Coverage is which required claims a candidate supplies and which
it does not; whether those claims will agree with the paper is not an input, is not knowable yet, and
must never become one.
"""

from __future__ import annotations

from app.services.validation_checks import AUTHOR_RESULTS, AVAILABLE
from app.services.validation_scorecard import CATEGORY_WEIGHTS

# The ranking contract, versioned so a persisted selection says which rules chose it.
RANKING_VERSION = 1
RANKING_POLICY = "complete_findings_then_weight_then_cost"
EXPLICIT_CHOICE = "explicit_choice"

# What each check costs, relatively. An eligible check against the authors' published results reads a
# table bioAF already holds; a reanalysis launches a workflow. Preferring the cheaper one where both
# are eligible is plan_8_3's rule, not a judgement about which is better evidence.
_COST = {AUTHOR_RESULTS: 0, "qc_metric": 1, "processed_reanalysis": 2, "raw_reanalysis": 3}
_DEFAULT_COST = 2


def _cost_of(check: str) -> int:
    return _COST.get(check, _DEFAULT_COST)


def analysis_candidates(pairs: list[dict], *, targets: list[dict], contrasts: list[dict]) -> list[dict]:
    """The (claim, check) pairs grouped into the analyses that would produce them.

    Claims of one check, one experiment and one contrast share one statistical output, so they are one
    candidate carrying every comparison it can validly supply.
    """
    grouped: dict[tuple, dict] = {}
    for pair in pairs or []:
        index = pair.get("claim_index")
        if not isinstance(index, int) or index >= len(targets or []):
            continue
        target = targets[index] or {}
        contrast_index = target.get("contrast_index") if isinstance(target.get("contrast_index"), int) else None
        experiment_id = target.get("reported_experiment_id")
        if experiment_id is None and contrast_index is not None and contrast_index < len(contrasts or []):
            experiment_id = (contrasts[contrast_index] or {}).get("reported_experiment_id")
        key = (str(pair.get("check")), str(experiment_id), contrast_index)
        candidate = grouped.setdefault(
            key,
            {
                "analysis_key": ":".join(str(part) for part in key),
                "check": key[0],
                "experiment_id": experiment_id,
                "contrast_index": contrast_index,
                "claim_indices": [],
                "prerequisites": [],
                "cost": _cost_of(key[0]),
            },
        )
        candidate["claim_indices"].append(index)
        if pair.get("status") != AVAILABLE and pair.get("requirement"):
            # A requirement the run itself establishes still has to be established before the run is
            # a runnable option, and saying which one is what makes it actionable.
            candidate["prerequisites"].append(str(pair["requirement"]))
    for candidate in grouped.values():
        candidate["claim_indices"].sort()
        candidate["prerequisites"] = sorted(set(candidate["prerequisites"]))
        candidate["ready"] = not candidate["prerequisites"]
    return sorted(grouped.values(), key=lambda c: c["analysis_key"])


def coverage_of(candidate: dict, *, inventory: dict, settled: list[int] | None = None) -> dict:
    """Which findings this candidate would complete, which it would only start, and what that is worth.

    ``settled`` are claims a valid outcome already governs, so a candidate that supplies a finding's
    remaining claims completes it without redoing the work already done.
    """
    supplied = set(candidate.get("claim_indices") or []) | set(settled or [])
    completes: list[str] = []
    partial: list[str] = []
    outstanding: dict[str, list[int]] = {}
    weight = 0
    for finding in (inventory or {}).get("findings") or []:
        if not isinstance(finding, dict):
            continue
        required = [c for c in finding.get("required") or finding.get("claim_indices") or [] if isinstance(c, int)]
        if not required or not set(required) & set(candidate.get("claim_indices") or []):
            continue
        missing = [c for c in required if c not in supplied]
        if missing:
            partial.append(finding["id"])
            outstanding[finding["id"]] = missing
            continue
        completes.append(finding["id"])
        category = (finding.get("importance") or {}).get("category")
        weight += CATEGORY_WEIGHTS.get(category, 0)
    return {
        "completes": completes,
        "partial": partial,
        "outstanding": outstanding,
        "weight": weight,
        "claims_supplied": sorted(supplied & set(candidate.get("claim_indices") or [])),
    }


def rank_analyses(
    candidates: list[dict],
    *,
    inventory: dict,
    settled: list[int] | None = None,
    chosen_claim: int | None = None,
) -> list[dict]:
    """Candidates in the order to prefer them, each carrying its coverage and why it ranks where it does.

    Feasibility first: a candidate whose prerequisites are open is not a runnable option and ranks
    below every ready one, however much it would cover. Then the weight of what it would complete,
    then how many findings that is, then the cheaper check, then a stable key.

    ``chosen_claim`` is a declared choice. An automated ranking never replaces one: the candidate that
    holds it leads, and says that is why.
    """
    scored = []
    for candidate in candidates or []:
        cover = coverage_of(candidate, inventory=inventory, settled=settled)
        scored.append({**candidate, "coverage": cover})
    explicit = [c for c in scored if isinstance(chosen_claim, int) and chosen_claim in c["claim_indices"]]

    def _key(candidate: dict):
        cover = candidate["coverage"]
        return (
            0 if candidate.get("ready") else 1,
            -cover["weight"],
            -len(cover["completes"]),
            candidate.get("cost", _DEFAULT_COST),
            -len(cover["partial"]),
            candidate["analysis_key"],
        )

    ordered = explicit + sorted((c for c in scored if c not in explicit), key=_key)
    for position, candidate in enumerate(ordered, start=1):
        candidate["ranking"] = {
            "policy": EXPLICIT_CHOICE if candidate in explicit else RANKING_POLICY,
            "version": RANKING_VERSION,
            "rank": position,
        }
    return ordered
