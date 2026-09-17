"""plan_8_1 section 2.2: the finding inventory, proposed in its own call over the committed claims.

plan_8 asked the extraction call for the inventory too. That grew the one answer that studies 42 and 43
lost at the output limit, and it meant a failed inventory took the claims with it. Now the extraction is
committed first, and this stage groups ITS claims into findings:

- **Input:** the paper's text, and the committed claims with their indices, experiments and contrasts.
- **Output:** the findings under the fixed rubric. The model proposes a category; the rubric's weight is
  never asked for. It runs before anything is measured, so no result can shape it.
- **Its own measured budget** (``read_measurements/inventory.json``) and its own prompt fingerprint.
- **One bounded retry in total:** two submissions per cycle, shared across truncation (a larger budget)
  and a rejected proposal (the same budget, showing each problem and failed quote). Attempts are committed
  before they go out, so a restart never grants another. A second failure of any kind ends the cycle with
  its cause; the committed claims are untouched.

Validation is plan_8's (``validation_finding_inventory``), with section 2.3's split: a proposal whose
membership holds and whose importance does not is provisional, not a failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from app.services import validation_decision_budgets as budgets
from app.services.llm_decision import decide, fenced_json

logger = logging.getLogger("bioaf.validation_inventory")

INVENTORY_STAGE = "inventory_stage"
INVENTORY_INTENT = "grouping the paper's claims into findings"
PENDING = "pending"
PENDING_REASON = "The findings are being established."

ATTEMPT_OK = "ok"
ATTEMPT_REJECTED = "rejected"

_KEPT_ANSWER_CHARS = 100_000

_SCHEMA = (
    '{"findings": [{"description": "the finding in a few words", "claim_indices": [0], '
    '"importance": "primary | supporting | technical", '
    '"rationale": "one sentence: the finding\'s role in the paper\'s conclusions", '
    '"quote": "the paper\'s exact words presenting it as a main result or as supporting one", '
    '"prerequisite_for": ["for a technical check, the indices in findings of the findings it enables"]}]}'
)

_SYSTEM = (
    "You are establishing the FINDINGS a paper reports, before anything is measured. You are given the "
    "paper's full text and the claims already read from it, each with its index, its experiment and the "
    "contrast it reports on. Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else, "
    f"matching exactly this schema:\n\n{_SCHEMA}\n\n"
    "Group the claims into FINDINGS, the distinct computational results the paper reports. Place every claim "
    "you are given in exactly one finding, by its index in the list, and use no other index. Claims that "
    "together establish one result, such as the genes up and the genes down in one comparison, or a set "
    "and its subset, are one finding, never several. Give each finding one importance category under this "
    "fixed rubric: primary, a distinct computational finding necessary to support a main conclusion of the "
    "paper; supporting, a substantive computational finding that supports, extends or qualifies the main "
    "conclusions without independently being necessary to establish them; technical, an operational or "
    "contextual check (sequencing depth, alignment rate, sample counts, quality control) that enables "
    "assessment but does not itself establish a scientific finding. The category follows the finding's "
    "scientific role in the paper, never its metric name, how easy it is to check, or whether its data are "
    "accessible. Give a one-sentence rationale naming that role, and for a primary or supporting finding "
    "quote the paper's exact words, copied character for character from its text, that present it as a "
    "main result or as supporting one. Never give a numeric weight. For a technical check, list in "
    "prerequisite_for the findings whose assessment depends on it."
)


def build_inventory_prompt(full_text: str, *, claims: list[dict], experiments: list[dict], contrasts: list[dict]):
    """``(system, payload)``. The system prompt is fixed, so its fingerprint pins the measurement; the
    claims, experiments, contrasts and the paper travel in the payload."""
    lines = []
    for index, claim in enumerate(claims):
        text = (claim.get("claim_text") or claim.get("metric_key") or "").strip()
        where = []
        if claim.get("reported_experiment_id"):
            where.append(f"experiment {claim['reported_experiment_id']}")
        contrast_index = claim.get("contrast_index")
        if isinstance(contrast_index, int) and 0 <= contrast_index < len(contrasts):
            where.append(f"contrast: {(contrasts[contrast_index] or {}).get('name') or contrast_index}")
        if claim.get("source_locator"):
            where.append(str(claim["source_locator"]))
        lines.append(f"[{index}] {text}" + (f" ({'; '.join(where)})" if where else ""))
    experiment_lines = [
        f"- {e.get('id')}: {e.get('assay') or 'assay not stated'}"
        + (f", {e['description']}" if e.get("description") else "")
        for e in experiments or []
        if isinstance(e, dict)
    ]
    contrast_lines = [f"- [{i}] {(c or {}).get('name') or 'unnamed'}" for i, c in enumerate(contrasts or [])]
    payload = "Claims read from the paper:\n" + ("\n".join(lines) or "(none)")
    if experiment_lines:
        payload += "\n\nThe paper's experiments:\n" + "\n".join(experiment_lines)
    if contrast_lines:
        payload += "\n\nThe paper's contrasts:\n" + "\n".join(contrast_lines)
    payload += f"\n\nPaper full text:\n\n{full_text}"
    return _SYSTEM, payload


def inventory_fingerprint() -> str:
    from app.services.validation_read_budget import fingerprint

    system, _ = build_inventory_prompt("", claims=[], experiments=[], contrasts=[])
    return fingerprint(system)


def pending_inventory() -> dict:
    """What a plan carries between its committed claims and the inventory stage."""
    from app.services.validation_scorecard import RUBRIC_VERSION

    return {
        "rubric_version": RUBRIC_VERSION,
        "revision": None,
        "status": PENDING,
        "reason": PENDING_REASON,
        "findings": [],
        "unplaced_claims": [],
        "history": [],
    }


def failed_inventory(cause: str, *, previous: dict | None = None) -> dict:
    """section 2.1: the stage could not establish the findings. The claims stand; the scope does not."""
    from app.services.validation_read_failure import inventory_failure_reason

    inventory = dict(previous) if isinstance(previous, dict) and previous.get("findings") else pending_inventory()
    inventory.update(status="unresolved", failed=True, cause=cause, reason=inventory_failure_reason(cause))
    return inventory


@dataclass
class InventoryResult:
    inventory: dict
    failed: bool
    cause: str | None = None
    issues: list[dict] = dataclass_field(default_factory=list)


def _problems(inventory: dict) -> list[str]:
    """Every problem the rubric found in a proposal, each failed quote quoted, for the retry to show."""
    problems: list[str] = []
    for finding in inventory.get("findings") or []:
        for problem in (finding.get("membership") or {}).get("problems") or []:
            problems.append(f"{finding['id']}: {problem}")
        importance = finding.get("importance") or {}
        if importance.get("status") != "validated" and importance.get("problem"):
            quoted = f' (quote: "{importance["quote"]}")' if importance.get("quote") else ""
            problems.append(f"{finding['id']}: {importance['problem']}{quoted}")
    unplaced = inventory.get("unplaced_claims") or []
    if unplaced:
        numbered = ", ".join(str(i) for i in unplaced)
        problems.append(f"claim index {numbered} belongs to no finding")
    if not problems and inventory.get("status") == "unresolved":
        problems.append(str(inventory.get("reason") or "the proposal could not be read as findings"))
    return problems


def _rejection_note(problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "\n\nYour previous proposal was rejected under the rubric:\n"
        f"{listed}\n\n"
        "Propose the findings again. Place every claim index in exactly one finding, and copy each quote "
        "character for character from the paper's text."
    )


def _cause(attempts: list[dict]) -> str:
    from app.services.validation_extraction_service import _attempt_cause

    def one(attempt: dict) -> str:
        if attempt.get("outcome") == ATTEMPT_REJECTED:
            return f"the proposal was rejected ({'; '.join(attempt.get('problems') or [])})"
        return _attempt_cause(attempt)

    if not attempts:
        return "bioAF hit an internal error"
    if len(attempts) == 1:
        return one(attempts[0])
    return f"{one(attempts[0])}; the recovery attempt failed too: {one(attempts[-1])}"


def _issue(decision, *, attempt: dict, impact: str, problems: list[str] | None = None) -> dict:
    from app.models.validation_study_issue import OUTCOME_INCOMPLETE

    if problems:
        row = {
            "step": INVENTORY_INTENT,
            "outcome": OUTCOME_INCOMPLETE,
            "impact": impact,
            "message": f"The model's proposal while {INVENTORY_INTENT} did not meet the rubric bioAF validates it against.",
            "model": decision.model,
        }
    else:
        row = decision.as_issue(impact=impact) or {}
    detail = {
        "attempt": attempt.get("attempt"),
        "max_tokens": attempt.get("max_tokens"),
        "output_tokens": decision.output_tokens,
        "stop_reason": decision.stop_reason,
        "elapsed_seconds": decision.elapsed_seconds,
        "problems": "; ".join(problems) if problems else None,
        "answer_text": (decision.text or "")[:_KEPT_ANSWER_CHARS] or None,
    }
    row["technical_detail"] = {k: v for k, v in detail.items() if v is not None}
    return row


async def run_inventory_stage(
    study,
    *,
    full_text: str,
    targets: list[dict],
    experiments: list[dict],
    contrasts: list[dict],
    client,
    cfg,
    checkpoint=None,
) -> InventoryResult:
    """Run (or resume) the inventory cycle over the committed claims. Never raises for a model's failure."""
    from app.services import validation_read_budget as read_budget
    from app.services import validation_read_cycle as cycles
    from app.services.validation_finding_inventory import inventory_from_proposal

    if not targets:
        # No committed claim: there is nothing to group, and no call is made to say so.
        inventory = inventory_from_proposal([], targets=[], full_text=full_text, decided_by={"kind": "rule"})
        return InventoryResult(inventory=inventory, failed=False)

    budget = read_budget.budget_for(read_budget.INVENTORY, cfg.model)
    if (cycles.current_cycle(study.evidence_json, INVENTORY_STAGE) or {}).get("status") != cycles.IN_PROGRESS:
        cycles.begin_cycle(study, INVENTORY_STAGE, model=cfg.model, provider=cfg.provider)
    if not (cycles.current_cycle(study.evidence_json, INVENTORY_STAGE) or {}).get("budget"):
        cycles.update_cycle(study, INVENTORY_STAGE, budget=budget.provenance())
    cycles.mark_interrupted(study, INVENTORY_STAGE)
    system, payload = build_inventory_prompt(full_text, claims=targets, experiments=experiments, contrasts=contrasts)
    issues: list[dict] = []

    async def _commit() -> None:
        if checkpoint is not None:
            await checkpoint()

    def _attempts() -> list[dict]:
        return list((cycles.current_cycle(study.evidence_json, INVENTORY_STAGE) or {}).get("attempts") or [])

    def _planned(previous: dict | None) -> tuple[int, str] | None:
        if previous is None:
            return budget.max_tokens, ""
        spent = int(previous.get("max_tokens") or budget.max_tokens)
        outcome = previous.get("outcome")
        if outcome == "truncated":
            larger = read_budget.recovery_budget(
                spent, model=cfg.model, provider=cfg.provider, record=read_budget.load_record(read_budget.INVENTORY)
            )
            return (larger, "") if larger is not None else None
        if outcome == ATTEMPT_REJECTED:
            return spent, _rejection_note(previous.get("problems") or [])
        if outcome == cycles.ATTEMPT_INTERRUPTED:
            return spent, ""
        return None

    def _end(cause: str, last: dict | None = None) -> InventoryResult:
        cycles.finish_cycle(study, INVENTORY_STAGE, status=cycles.FAILED, cause=cause)
        return InventoryResult(
            inventory=failed_inventory(cause, previous=last), failed=True, cause=cause, issues=issues
        )

    last_inventory: dict | None = None
    while len(_attempts()) < cycles.MAX_SUBMISSIONS:
        previous = _attempts()[-1] if _attempts() else None
        planned = _planned(previous)
        if planned is None:
            return _end(_cause(_attempts()), last_inventory)
        max_tokens, note = planned
        attempt = cycles.start_attempt(study, INVENTORY_STAGE, max_tokens=max_tokens, model=cfg.model)
        await _commit()
        decision = await decide(
            intent=INVENTORY_INTENT,
            system=system,
            payload=payload + note,
            client=client,
            model=cfg.model,
            api_key=cfg.api_key,
            max_tokens=max_tokens,
            purpose=budgets.FINDING_INVENTORY,
        )
        usage = {
            "output_tokens": decision.output_tokens,
            "stop_reason": decision.stop_reason,
            "elapsed_seconds": decision.elapsed_seconds,
        }
        logger.info(
            "study %s: inventory attempt %d: outcome=%s max_tokens=%d output_tokens=%s elapsed=%ss",
            study.id,
            attempt["attempt"],
            decision.outcome,
            max_tokens,
            decision.output_tokens,
            decision.elapsed_seconds,
        )
        more = len(_attempts()) < cycles.MAX_SUBMISSIONS
        if decision.ok:
            data = decision.data or fenced_json(decision.text) or {}
            raw = data.get("findings") if isinstance(data.get("findings"), list) else None
            inventory = inventory_from_proposal(
                raw,
                targets=targets,
                full_text=full_text,
                decided_by={"kind": "model", "model": cfg.model},
            )
            problems = _problems(inventory)
            if not problems or (not more and inventory.get("status") != "unresolved"):
                # Established, or (on the recovery attempt) membership held and only importance is open:
                # section 2.3's provisional scope, never a failure.
                cycles.finish_attempt(
                    study, INVENTORY_STAGE, outcome=ATTEMPT_OK, **({"problems": problems} if problems else {}), **usage
                )
                cycles.finish_cycle(study, INVENTORY_STAGE, status=cycles.SUCCEEDED)
                inventory["attempts"] = len(_attempts())
                await _commit()
                return InventoryResult(inventory=inventory, failed=False, issues=issues)
            finished = cycles.finish_attempt(
                study, INVENTORY_STAGE, outcome=ATTEMPT_REJECTED, problems=problems, **usage
            )
            last_inventory = inventory
            issues.append(
                _issue(decision, attempt=finished, impact="degraded" if more else "blocked", problems=problems)
            )
            await _commit()
            if not more:
                return _end(_cause(_attempts()), last_inventory)
            continue

        finished = cycles.finish_attempt(study, INVENTORY_STAGE, outcome=decision.outcome, **usage)
        recovers = more and decision.outcome == "truncated" and _planned(finished) is not None
        issues.append(_issue(decision, attempt=finished, impact="degraded" if recovers else "blocked"))
        await _commit()
        if not recovers:
            return _end(_cause(_attempts()), last_inventory)

    return _end(_cause(_attempts()), last_inventory)
