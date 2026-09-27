"""plan_8_7: related propositions are reconciled by reading what they SAY, before publication.

The owner agreed the correction on 2026-09-27:

    "Reconciliation examines meaning. The selected model assesses related propositions and evidence
    before publication. Citation and scope overlap identify review candidates; they do not decide
    contradiction or duplicate failures. Preserve compatible positives/negatives and distinct
    defects. Keep genuine unresolved conflicts visible."

The counterexample that made it necessary is a local probe: M1.A verified for described processing
beside M3.B failed for the inappropriate handling of repeated observations, on the same citation and
the same named analysis. Both are true. A method can be completely described and still be the wrong
method, and comparing the scopes two answers named cannot tell that apart from a contradiction, so
`validation_finding_overlap` converted the positive to undetermined.

What this pass does and does not do:

- **it finds nothing on its own.** `validation_finding_overlap.candidate_groups` proposes the groups;
  this reads them. A group is examined once, and a group of one is not examined at all.
- **three relations, and only three.** Compatible propositions both stand untouched. One demonstrated
  failure stated twice keeps one deduction, and the other records the cross-reference with its own
  finding held under ``withheld``. A genuine contradiction leaves the affected proposition
  unresolved, with the conflict on the record.
- **it cannot rewrite a measurement.** plan_8_7 section 5 replaces blanket measurement precedence with
  precedence for the same proposition and scope, and a model may not overturn a measured value.
- **a failure cannot certify consistency.** No model, an unreachable provider or an answer that names
  an obligation nobody asked about leaves the group unreconciled and says so.
"""

from __future__ import annotations

import logging

from app.services import validation_decision_budgets as budgets
from app.services.llm_decision import OUTCOME_OK, decide_with_recovery
from app.services.validation_rubric_v3 import CRITERIA_BY_ID, FAILED, UNDETERMINED, VERIFIED

logger = logging.getLogger("bioaf.validation_semantic_reconciliation")

# 1: the first version of the pass that reads propositions rather than citation sets.
RECONCILIATION_VERSION = 1

COMPATIBLE = "compatible"
DUPLICATE = "duplicate"
CONTRADICTION = "contradiction"
RELATIONS = (COMPATIBLE, DUPLICATE, CONTRADICTION)

PERFORMED = "performed"
NOT_PERFORMED = "not_performed"
NOTHING = "nothing_to_reconcile"

INTENT = "reconciling related propositions about one paper before they are published"

# What one group may cost. plan_8_7 section 6: "Reuse the bounded initial/recovery allowance for each
# reconciliation work item", which is `decide_with_recovery`'s own one-retry policy, and "avoid an
# unbounded all-pairs review": a group is examined once and the number of groups is bounded by the
# number of obligations.
MAX_GROUPS = 18
# How much of a cited passage travels with the pair. The propositions are short; the passage they
# both rest on is what decides whether they are about the same thing.
MAX_PASSAGE_CHARS = 1200

_SYSTEM = (
    "Two or more findings about one scientific paper rest on the same evidence. You are deciding, for "
    "each PAIR, how the two propositions relate. You are not re-judging either finding and not "
    "assessing the paper.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"pairs": [{"a": "the first finding id", "b": "the second finding id", "relation": '
    '"compatible | duplicate | contradiction", "reason": "one or two sentences, from the evidence", '
    '"resolve": "for a contradiction only: which of the two is not established, or omit it"}], '
    '"confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- `compatible` is the ordinary answer and it is what shared evidence usually means. Two "
    "propositions can BOTH be true of the same passage: a method can be completely described and "
    "still be the wrong method for the data it was applied to.\n"
    "- `duplicate` means the two findings are the SAME demonstrated failure stated twice, with the "
    "same consequence. Two different defects that happen to cite one paragraph are not duplicates.\n"
    "- `contradiction` means the two propositions cannot both be true. Use it only when one of them "
    "must be wrong, and say which in `resolve` if the evidence shows it. Where the evidence does not "
    "show which, omit `resolve` and the conflict is reported to a person.\n"
    "- reason from the evidence quoted below, not from the wording of the findings.\n"
    "- answer about every pair listed, and about no other."
)


def _statement(leaf: str) -> str:
    criterion_id, _, obligation = str(leaf).partition(".")
    criterion = CRITERIA_BY_ID.get(criterion_id)
    if criterion is None:
        return leaf
    half = criterion.b if obligation == "B" else criterion.a
    return f"{criterion.title}: {half}"


def _rank(judgment: dict) -> tuple[int, int, str]:
    """Which of two duplicates keeps the deduction: the better-evidenced method, then the wider evidence."""
    method = str(judgment.get("method") or "model_assisted")
    order = {"measurement": 3, "human_assisted": 2, "model_assisted": 1}.get(method, 1)
    return (order, len((judgment.get("evidence") or {}).get("citations") or []), "")


def _measured(judgment: dict) -> bool:
    return str(judgment.get("method") or "") == "measurement"


def _pairs_of(group: list[str]) -> list[tuple[str, str]]:
    return [(group[i], group[j]) for i in range(len(group)) for j in range(i + 1, len(group))]


def _payload(group: list[str], judgments: dict[str, dict], passages: dict[str, str]) -> str:
    lines: list[str] = []
    cited: list[str] = []
    for leaf in group:
        judgment = judgments[leaf]
        outcome = str(judgment.get("outcome"))
        citations = [str(c) for c in (judgment.get("evidence") or {}).get("citations") or []]
        cited += [c for c in citations if c not in cited]
        lines.append(
            f"Finding {leaf} ({'supported' if outcome == VERIFIED else 'a problem'}).\n"
            f"  Obligation: {_statement(leaf)}\n"
            f"  What it concluded: {judgment.get('rationale') or 'nothing recorded'}\n"
            f"  What it says it is about: {judgment.get('scope') or 'not stated'}\n"
            + (f"  Its stated consequence: {judgment['impact']}\n" if judgment.get("impact") else "")
            + f"  Cites: {', '.join(citations) or 'nothing'}"
        )
    evidence = "\n\n".join(
        f"[{key}] {str(passages.get(key) or '')[:MAX_PASSAGE_CHARS]}" for key in cited if passages.get(key)
    )
    pairs = ", ".join(f"{a} with {b}" for a, b in _pairs_of(group))
    return (
        "Findings:\n\n"
        + "\n\n".join(lines)
        + f"\n\nPairs to answer about: {pairs}."
        + (f"\n\nThe evidence they rest on:\n{evidence}" if evidence else "\n\nbioAF holds none of the cited passages.")
    )


def _validate(group: list[str]):
    def check(data: dict) -> list[str]:
        problems: list[str] = []
        rows = [p for p in (data or {}).get("pairs") or [] if isinstance(p, dict)]
        if not rows:
            return ["`pairs` must carry one entry per pair listed in the question"]
        wanted = {frozenset(pair) for pair in _pairs_of(group)}
        for row in rows:
            named = frozenset({str(row.get("a") or ""), str(row.get("b") or "")})
            if named not in wanted:
                problems.append(
                    f"{sorted(named)} is not one of the pairs asked about; answer about "
                    + ", ".join(f"{a}/{b}" for a, b in _pairs_of(group))
                )
            if str(row.get("relation") or "").strip().lower() not in RELATIONS:
                problems.append("`relation` must be exactly one of " + ", ".join(RELATIONS))
            if not str(row.get("reason") or "").strip():
                problems.append("every pair states the `reason` it was read that way")
        return problems

    return check


def _demote(judgment: dict, rationale: str, **extra) -> dict:
    """Keep the finding, stop it deducting. The reader still sees what bioAF found."""
    return {
        "outcome": UNDETERMINED,
        "rationale": rationale,
        "scope": judgment.get("scope"),
        "method": judgment.get("method"),
        "assessor": judgment.get("assessor"),
        "coverage": judgment.get("coverage"),
        "evidence": judgment.get("evidence"),
        "evidence_ids": judgment.get("evidence_ids"),
        "withheld": {
            key: value for key, value in judgment.items() if key in ("outcome", "rationale", "impact", "finding_scope")
        },
        "reconciliation_version": RECONCILIATION_VERSION,
        **extra,
    }


async def reconcile_semantically(
    *,
    judgments: dict[str, dict] | None,
    passages: dict[str, str] | None = None,
    client,
    model: str,
    api_key: str | None,
    groups: list[list[str]] | None = None,
) -> dict:
    """``{"judgments", "status", "examined", "unresolved", "unreconciled", "reason"}``. Never raises.

    ``passages`` maps a citation id to its text, so the pass reasons from the evidence rather than
    from the wording of the two findings.
    """
    from app.services.validation_finding_overlap import candidate_groups

    rows = {leaf: j for leaf, j in (judgments or {}).items() if isinstance(j, dict)}
    found = dict(judgments or {})
    candidates = [g for g in (groups if groups is not None else candidate_groups(rows)) if len(g) > 1][:MAX_GROUPS]
    if not candidates:
        return _record(found, NOTHING, reason=None)
    if client is None or not model:
        return _record(
            found,
            NOT_PERFORMED,
            reason="no language model is configured for this organisation, so related findings were not reconciled",
            unreconciled=candidates,
        )
    examined = 0
    unresolved: list[dict] = []
    unreconciled: list[list[str]] = []
    for group in candidates:
        decision = await decide_with_recovery(
            intent=INTENT,
            system=_SYSTEM,
            payload=_payload(group, rows, passages or {}),
            client=client,
            model=model,
            api_key=api_key,
            purpose=budgets.SEMANTIC_RECONCILIATION,
            validate=_validate(group),
        )
        if decision.outcome != OUTCOME_OK:
            logger.info("a group of related findings could not be reconciled (%s): %s", decision.outcome, group)
            unreconciled.append(group)
            continue
        examined += 1
        _apply(decision.data, rows, found, unresolved, model=model)
    status = PERFORMED if examined else NOT_PERFORMED
    return _record(
        found,
        status,
        reason=None
        if examined
        else "every group of related findings was left unreconciled, so bioAF has not established that they agree",
        examined=examined,
        unresolved=unresolved,
        unreconciled=unreconciled,
    )


def _apply(data: dict, rows: dict[str, dict], found: dict, unresolved: list[dict], *, model: str) -> None:
    """One answer, over the findings it is about. Nothing outside the pair it names is touched."""
    for row in (data or {}).get("pairs") or []:
        if not isinstance(row, dict):
            continue
        a, b = str(row.get("a") or ""), str(row.get("b") or "")
        if a not in rows or b not in rows:
            continue
        relation = str(row.get("relation") or "").strip().lower()
        reason = str(row.get("reason") or "").strip()
        if relation == COMPATIBLE:
            continue
        if relation == DUPLICATE:
            if rows[a].get("outcome") != FAILED or rows[b].get("outcome") != FAILED:
                # Only two demonstrated failures can be the same failure twice.
                continue
            owner, other = (a, b) if _rank(rows[a]) >= _rank(rows[b]) else (b, a)
            if found.get(other, {}).get("outcome") != FAILED:
                continue
            found[other] = _demote(
                rows[other],
                f"bioAF found this under {owner} as well: {reason}. One demonstrated failure is one deduction; "
                f"see {owner} for the finding and what it costs",
                same_finding_as=owner,
                reconciled_by=model,
            )
            continue
        if relation != CONTRADICTION:
            continue
        resolve = str(row.get("resolve") or "").strip()
        if resolve in (a, b):
            against = b if resolve == a else a
            if _measured(rows[resolve]):
                # plan_8_7 section 5: a model may not rewrite a measured value.
                found[resolve] = {**found.get(resolve, rows[resolve]), "tension_with": against, "tension": reason}
                unresolved.append({"leaves": [a, b], "reason": reason, "held": "a measured outcome was not withdrawn"})
                continue
            found[resolve] = _demote(
                rows[resolve],
                f"{against} contradicts this and the contradiction was read from the evidence they share: {reason}",
                contradicted_by=against,
                reconciled_by=model,
            )
            continue
        # A conflict the evidence did not resolve. Both are reported and neither is withdrawn.
        for leaf, other in ((a, b), (b, a)):
            found[leaf] = {**found.get(leaf, rows[leaf]), "tension_with": other, "tension": reason}
        unresolved.append({"leaves": [a, b], "reason": reason, "held": "neither proposition was withdrawn"})


def _record(judgments: dict, status: str, *, reason, examined: int = 0, unresolved=None, unreconciled=None) -> dict:
    return {
        "judgments": judgments,
        "status": status,
        "reason": reason,
        "examined": examined,
        "unresolved": unresolved or [],
        "unreconciled": unreconciled or [],
        "reconciliation_version": RECONCILIATION_VERSION,
    }
