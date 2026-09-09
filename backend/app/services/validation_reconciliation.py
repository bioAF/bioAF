"""change_7.1 section 6: revise the provisional reading once the evidence is in hand.

The paper is read before anything has been fetched. That first interpretation is a guess made from
prose: it is allowed to be wrong, and for Groff et al. it was, in three ways that each read as a
finding about the paper. It becomes a DEFECT only when better evidence arrives and nothing revises
it, which is what the deployed rerun did. Study 32 discovered the sample table, the code and the
results table, and then reported the original prose reading beside them unchanged.

**Retrieval is not reconciliation.** Fetching a supplement and storing it changes no decision. The
inspected evidence has to reach the call that interprets the claims, and that call's answers have
to land on the persisted plan. Adding an optional prompt argument and never passing it satisfies
nothing.

**A revision is recorded, not silently applied.** The first reading and the reason for changing it
are both kept, because "we thought 54 and the sample table says 51" is more useful to a reader than
51 appearing with no history, and because a wrong revision has to be auditable too.

**A failure leaves the claim unresolved, never verified.** If reconciliation cannot answer, the
provisional value is not thereby confirmed; it is marked as not established and the evidence is
kept for a later attempt.

**Unchanged evidence is not re-reconciled.** The inventory is fingerprinted, so a retry or a
resumption does not spend a second model call to ask the same question of the same inputs.
"""

from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comparison_target import ComparisonTarget

from app.services.supplement_inventory import build_inventory_digest
from app.services.validation_extraction_service import bind_claims

logger = logging.getLogger("bioaf.validation_reconciliation")

# The claim-context fields a reconciliation decision may revise. `bound_key` and its provenance are
# handled separately because they carry the binding contract.
_CONTEXT_FIELDS = (
    "sample_subset",
    "qc_stage",
    "direction",
    "threshold",
    "threshold_kind",
    "output_type",
    "measurement_basis",
)


def inventory_fingerprint(supplements: list[dict] | None) -> str:
    """A stable identity for the evidence a reconciliation used.

    Section 6 asks that derived decisions be associated with the resource versions they used, and
    that a retry not redo work. Both need the same thing: a fingerprint of what was inspected.
    """
    material = [
        {
            "filename": s.get("filename"),
            "role": s.get("role"),
            "row_count": s.get("row_count"),
            "resolved": bool(s.get("resolved")),
        }
        for s in sorted(
            (s for s in (supplements or []) if isinstance(s, dict)),
            key=lambda s: str(s.get("filename") or s.get("label") or ""),
        )
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


async def reconcile(
    session: AsyncSession,
    study,
    plan,
    *,
    supplements: list[dict] | None,
    client,
    model: str,
    api_key: str | None,
    on_issue=None,
) -> dict:
    """Re-interpret this plan's claims against the inspected evidence. Never raises.

    Returns ``{"revisions": [...], "fingerprint": ...}``. A revision names the target, what it said
    before, what it says now, and why.
    """
    # Loaded explicitly: the relationship is lazy, and touching it here would attempt IO outside
    # the async context.
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
    resolved = [s for s in (supplements or []) if isinstance(s, dict) and s.get("resolved")]
    if not targets or not resolved:
        # Nothing inspected means no better answer is available, and re-asking the same question of
        # the same inputs would spend a model call to learn nothing.
        return {"revisions": [], "fingerprint": None}

    fingerprint = inventory_fingerprint(supplements)
    evidence = dict(study.evidence_json or {})
    if (evidence.get("reconciliation") or {}).get("fingerprint") == fingerprint:
        logger.info("study %s: evidence unchanged since the last reconciliation, skipping", study.id)
        return {"revisions": [], "fingerprint": fingerprint}

    digest = build_inventory_digest(resolved)
    claims = [
        {
            "metric_key": t.metric_key,
            "claim_text": t.claim_text,
            "value": t.claimed_value,
            "unit": t.unit,
            "source_locator": t.source_locator,
        }
        for t in targets
    ]

    try:
        decisions = await bind_claims(
            claims,
            client=client,
            model=model,
            api_key=api_key,
            inventory=digest,
            on_issue=on_issue,
        )
    except Exception as exc:  # noqa: BLE001 - reconciliation degrades the report, it cannot fail the study
        logger.warning("reconciliation failed for study %s: %s", study.id, exc)
        return {"revisions": [], "fingerprint": None}

    revisions = _apply(targets, decisions)
    evidence["reconciliation"] = {
        "fingerprint": fingerprint,
        "revisions": revisions,
        "model": model,
    }
    study.evidence_json = evidence
    await session.flush()
    return {"revisions": revisions, "fingerprint": fingerprint}


def _apply(targets: list, decisions: list[dict]) -> list[dict]:
    """Land each decision on its target, recording what changed and why.

    A decision that says nothing about a field leaves that field alone: reconciliation refines the
    provisional reading, it does not blank it.
    """
    revisions: list[dict] = []
    by_index = {d.get("claim_index"): d for d in decisions if isinstance(d, dict)}

    for index, target in enumerate(targets):
        decision = by_index.get(index)
        if decision is None:
            continue

        before = {field: getattr(target, field, None) for field in _CONTEXT_FIELDS}
        before["bound_key"] = target.bound_key
        changed = False

        for field in _CONTEXT_FIELDS:
            value = decision.get(field)
            if value is not None and getattr(target, field, None) != value:
                setattr(target, field, value)
                changed = True

        bound_by = decision.get("bound_by")
        if bound_by:
            # A reconciliation that could not answer marks the claim unresolved. The provisional
            # value is not confirmed by our failure to check it.
            target.bound_by = bound_by
            changed = True
        elif decision.get("bound_key") and decision["bound_key"] != target.bound_key:
            target.bound_key = decision["bound_key"]
            target.bound_by = "model"
            changed = True

        if decision.get("reason"):
            target.binding_reason = decision["reason"]
        if decision.get("confidence") is not None:
            target.binding_confidence = decision["confidence"]

        if changed:
            after = {field: getattr(target, field, None) for field in _CONTEXT_FIELDS}
            after["bound_key"] = target.bound_key
            revisions.append(
                {
                    "metric_key": target.metric_key,
                    "claim_text": target.claim_text,
                    "before": before,
                    "after": after,
                    "reason": decision.get("reason") or "",
                }
            )
    return revisions
