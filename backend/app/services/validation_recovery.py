"""plan_8_2 section 2.1 and owner decision 5: an audited recovery of a study's checks, on request only.

Studies 44 and 45 hold comparisons made before bioAF bound a table to a claim's contrast, and a table read
by the old decoder. Nothing historical is rerun automatically: until a recovery runs, such an outcome is
shown as pending re-evaluation (section 1.1). A person asks for a recovery. The preview says what it will
reuse, what it will fetch again and what it will re-evaluate; the recovery then does exactly that, through
the same services a read and the check queue use:

- **fetch_passages**: the article's JATS is read again from Europe PMC to record the passages that cite
  each supplement (the binding contract's verbatim evidence). No model is asked.
- **recheck_supplements**: the article's supplementary bundle is retrieved again and each claim is checked
  against each results table it is bound to.
- **reevaluate_checks**: the dependent consistency checks are superseded (the prior revision kept whole,
  with the reason) and re-evaluated by the check queue within bioAF's limits for checks before approval.
  Checks bound under the current rules are left as they are.
- **refresh_projection**: the claims' capabilities and the scorecard are rebuilt from the evidence, the
  prior scorecard kept in history.

A recovery never launches a workflow and asks no model. A reanalysis whose input the earlier decoder
refused is listed as needing a new run, which follows the approval gate. The prior plan revision,
outcomes, classification and scorecard are kept in ``evidence.recovery_history`` with the actor, the
build, the reason and the affected checks, and an audit row records the request. The stored rubric version
is honoured: nothing here changes the finding inventory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services import validation_check_queue as queue

# plan_8_2 labels, pending the owner's sign-off.
WHY_UNBOUND = "compared before bioAF established which contrast its table reports"
WHY_UNCHOSEN = "its table was chosen before bioAF bound tables to contrasts"
WHY_BINDING_VERSION = "bound under an earlier version of bioAF's binding rules"
WHY_DECODER = "read by an earlier version of bioAF's table decoder"
WHY_WORKFLOW_DECODER = (
    "its input was refused by an earlier version of bioAF's table decoder; a new run needs an approval"
)

_BUSY = ("requested", "acquiring_text", "reading")
_COMPARED = ("agree", "disagree", "unresolved", "not_checkable")


class PreviewChanged(Exception):
    """The recovery would no longer do what the person was shown."""


class RecoveryRefused(Exception):
    """This study cannot be recovered now; the words say why."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def why_affected(record: dict) -> str | None:
    """Why a consistency check's outcome must be re-evaluated, or None when it stands (``record_dict`` shape)."""
    from app.services.table_decoding import DECODER_VERSION
    from app.services.validation_table_binding import BINDING_VERSION

    if record.get("kind") != queue.AUTHOR_RESULTS or record.get("state") in (
        queue.PENDING,
        queue.RUNNING,
        queue.SUPERSEDED,
    ):
        return None
    outcome = record.get("outcome") or {}
    deps = record.get("dependencies") or {}
    bound = outcome.get("binding")
    if isinstance(bound, dict):
        if bound.get("version") != BINDING_VERSION:
            return WHY_BINDING_VERSION
        attempts = record.get("attempts") or []
        decoding = (attempts[-1] if attempts else {}).get("decoding") or {}
        if decoding and decoding.get("decoder_version") != DECODER_VERSION:
            return WHY_DECODER
        return None
    if outcome.get("superseded"):
        return WHY_UNBOUND
    if (outcome.get("table") or deps.get("table")) and outcome.get("outcome") in _COMPARED:
        return WHY_UNBOUND
    if "binding_version" not in deps:
        return WHY_UNCHOSEN
    return None


def _needs_approval(records: list[dict], evidence: dict) -> list[dict]:
    """Reanalyses the earlier decoder stopped at their input. Listed; never rerun by a recovery."""
    limitations = [row for row in (evidence.get("completion") or {}).get("limitations") or [] if isinstance(row, dict)]
    refused = [
        row
        for row in limitations
        if row.get("kind") == "input_unreadable" and "format looks binary" in str(row.get("detail") or "")
    ]
    if not refused:
        return []
    return [
        {"check_id": r.get("check_id"), "kind": r.get("kind"), "why": WHY_WORKFLOW_DECODER}
        for r in records
        if r.get("kind") in queue.WORKFLOW_KINDS and r.get("state") in (queue.UNRESOLVED, queue.BLOCKED)
    ]


def _supplements_missing_passages(evidence: dict) -> bool:
    return any(
        isinstance(s, dict) and s.get("kind") == "attachment" and "citing_passages" not in s
        for s in evidence.get("supplements") or []
    )


def _unbound_supplement_comparisons(evidence: dict) -> int:
    return sum(
        1
        for s in evidence.get("supplements") or []
        if isinstance(s, dict)
        for r in s.get("consistency") or []
        if isinstance(r, dict) and "binding" not in r
    )


def recovery_projection(evidence: dict, checks: list[dict]) -> dict:
    """What the report shows about recovery: whether one would change anything, and the last one run."""
    affected = [c for c in checks or [] if why_affected(c)]
    pmcid = bool((evidence or {}).get("pmcid"))
    unbound = _unbound_supplement_comparisons(evidence or {})
    history = (evidence or {}).get("recovery_history") or []
    last = history[-1] if history else None
    return {
        "available": bool(affected) or (pmcid and unbound > 0),
        "affected_count": len(affected),
        "last": (
            {k: last.get(k) for k in ("at", "actor", "reason", "requeued", "build")} if isinstance(last, dict) else None
        ),
    }


async def _plan_and_records(session, study):
    from app.services.validation_assessment import active_plan

    plan = await active_plan(session, study)
    if plan is None:
        return None, []
    records = [
        r for r in await queue.records_for(session, study.id) if str(r.check_id or "").startswith(f"plan:{plan.id}:")
    ]
    return plan, records


async def preview_recovery(session, study) -> dict:
    """What a recovery of this study would do. Changes nothing."""
    plan, records = await _plan_and_records(session, study)
    evidence = study.evidence_json or {}
    dicts = [queue.record_dict(r) for r in records]
    affected = [
        {
            "check_id": d["check_id"],
            "comparison_target_id": d["comparison_target_id"],
            "revision": d["revision"],
            "state": d["state"],
            "outcome": (d.get("outcome") or {}).get("outcome"),
            "why": why_affected(d),
        }
        for d in dicts
        if why_affected(d)
    ]
    pmcid = (evidence.get("pmcid") or "").strip()
    actions: list[dict] = []
    if plan is not None and pmcid and _supplements_missing_passages(evidence):
        actions.append(
            {
                "kind": "fetch_passages",
                "label": "Read the passages that cite each supplement",
                "detail": f"bioAF reads the article's full text from Europe PMC ({pmcid}) again and records, for each "
                "supplement, the paragraphs and legend that cite it. No model is asked.",
            }
        )
    if plan is not None and pmcid and _unbound_supplement_comparisons(evidence):
        actions.append(
            {
                "kind": "recheck_supplements",
                "label": "Check each claim against the supplements bound to it",
                "detail": "bioAF retrieves the article's supplementary bundle again and compares each claim only with a "
                "results table bound to its contrast. The earlier comparisons are kept in history.",
            }
        )
    if affected:
        actions.append(
            {
                "kind": "reevaluate_checks",
                "label": f"Re-evaluate {len(affected)} consistency {'check' if len(affected) == 1 else 'checks'}",
                "detail": "Each is superseded, its outcome kept whole in history, and re-evaluated by the check queue "
                "within bioAF's limits for checks before approval. Checks bound under the current rules are not rerun.",
            }
        )
    if actions:
        actions.append(
            {
                "kind": "refresh_projection",
                "label": "Rebuild the claims' capabilities and the scorecard",
                "detail": "From the evidence as it then stands; the scorecard it replaces is kept in history. The "
                "finding inventory and its rubric version are unchanged.",
            }
        )
    needs_approval = _needs_approval(dicts, evidence)
    fingerprint = queue.fingerprint(
        {
            "actions": [a["kind"] for a in actions],
            "affected": [[a["check_id"], a["revision"], a["state"]] for a in affected],
            "plan": getattr(plan, "id", None),
        }
    )
    return {
        "available": bool(actions),
        "actions": actions,
        "affected_checks": affected,
        "needs_approval": needs_approval,
        "launches_workflow": False,
        "model_calls": 0,
        "fingerprint": fingerprint,
    }


def _merge_passages(evidence: dict, fetched_rows: list[dict]) -> int:
    """Copy the passages citing each supplement onto the study's rows, matched by the artifact's identity."""
    by_identity: dict[str, dict] = {}
    for row in fetched_rows or []:
        for key in (row.get("identity"), row.get("filename")):
            if key:
                by_identity.setdefault(str(key), row)
    updated = 0
    for row in evidence.get("supplements") or []:
        if not isinstance(row, dict) or row.get("kind") != "attachment":
            continue
        source = by_identity.get(str(row.get("identity") or "")) or by_identity.get(str(row.get("filename") or ""))
        if source is not None:
            row["citing_passages"] = list(source.get("citing_passages") or [])
            updated += 1
    return updated


async def run_recovery(session, study, *, user_id: int | None, preview_fingerprint: str | None = None) -> dict:
    """Carry out the recovery ``preview_recovery`` describes. Raises ``RecoveryRefused`` for a study being
    read and ``PreviewChanged`` when the preview the person saw no longer holds. Never launches a workflow."""
    from app.services.audit_service import log_action
    from app.services.literature.fulltext_service import FullTextFetchService
    from app.services.validation_assessment import resolve_study_supplements
    from app.services.validation_claim_capabilities import refresh_claim_capabilities
    from app.services.validation_consistency_checks import enqueue
    from app.services.validation_provenance import current_build
    from app.services.validation_report_summary import record_scorecard

    if study.state in _BUSY:
        raise RecoveryRefused("the study is being read; recover it once the read has finished")
    preview = await preview_recovery(session, study)
    if preview_fingerprint and preview_fingerprint != preview["fingerprint"]:
        raise PreviewChanged("the study changed since the preview was shown; review the recovery again")
    recovery_id = uuid.uuid4().hex[:12]
    reason = f"recovery {recovery_id}"
    kinds = {a["kind"] for a in preview["actions"]}
    result = {
        "recovery_id": recovery_id,
        "requeued": 0,
        "fetched_passages": 0,
        "rechecked_supplements": False,
        "affected_checks": preview["affected_checks"],
        "needs_approval": preview["needs_approval"],
    }
    if not preview["available"]:
        return result

    plan, records = await _plan_and_records(session, study)
    evidence = dict(study.evidence_json or {})
    prior = {
        "state": study.state,
        "classification": study.classification,
        "failure_reason": study.failure_reason,
        "scorecard_record": evidence.get("scorecard_record"),
        "supplement_comparisons": [
            {"supplement": s.get("filename") or s.get("label"), "consistency": s.get("consistency")}
            for s in evidence.get("supplements") or []
            if isinstance(s, dict) and s.get("consistency")
        ],
        "checks": [
            {"check_id": r.check_id, "revision": r.revision, "state": r.state, "outcome": r.outcome_json}
            for r in records
        ],
    }

    if "fetch_passages" in kinds:
        text = await FullTextFetchService.fetch(pmcid=evidence.get("pmcid"))
        if text is not None:
            result["fetched_passages"] = _merge_passages(evidence, text.supplements)
        study.evidence_json = dict(evidence)
    if "recheck_supplements" in kinds:
        evidence["supplements"] = await resolve_study_supplements(session, study, evidence)
        result["rechecked_supplements"] = True
        study.evidence_json = dict(evidence)
    await session.flush()

    affected_ids = {a["check_id"] for a in preview["affected_checks"]}
    affected_targets = {r.comparison_target_id for r in records if r.check_id in affected_ids}
    before = {r.id: r.revision for r in records}
    recorded_targets = {r.comparison_target_id for r in records if r.kind == queue.AUTHOR_RESULTS}
    ensured = await enqueue(session, study, plan, reason=reason, skip=recorded_targets - affected_targets)
    for record in records:
        if record.check_id in affected_ids and record.revision == before.get(record.id):
            await queue.supersede(session, record, reason=reason)
    result["requeued"] = sum(1 for r in records if r.check_id in affected_ids and r.state == queue.PENDING)
    result["created"] = sum(1 for r in ensured if r.id not in before)

    await refresh_claim_capabilities(session, study, plan)
    entry = {
        "id": recovery_id,
        "at": _now(),
        "actor": user_id,
        "build": current_build(),
        "reason": "re-evaluation under bioAF's current binding and decoding rules",
        "actions": sorted(kinds),
        "affected_checks": preview["affected_checks"],
        "needs_approval": preview["needs_approval"],
        "requeued": result["requeued"],
        "prior": prior,
    }
    evidence = dict(study.evidence_json or {})
    evidence["recovery_history"] = list(evidence.get("recovery_history") or []) + [entry]
    study.evidence_json = evidence
    if study.state == "classified":
        await record_scorecard(session, study, reason=reason, force=True)
    await log_action(
        session,
        user_id,
        "validation_study",
        study.id,
        "recovery",
        details={k: entry[k] for k in ("id", "actions", "requeued", "build", "reason")}
        | {"affected_checks": [a["check_id"] for a in preview["affected_checks"]]},
        previous_value={"state": prior["state"], "classification": prior["classification"]},
    )
    await session.flush()
    return result
