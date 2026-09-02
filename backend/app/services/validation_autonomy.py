"""plan_6 step 5: how much the model decides, and how those decisions are shown.

One org-scoped setting with two values.

``assisted`` (the default) is today's behaviour made explicit: the model proposes, and anything it
declined or was unsure of is surfaced at the C1 gate for a person to resolve.

``autonomous`` asks the model to choose rather than defer, and records how sure it was when it did.

**The C1 gate is human in both modes.** It authorises the plan and the money, and no setting moves
that. What autonomy governs is the science: which sample a claim refers to, which contrast the paper
tested, which reference build it used, and (step 8) whether the computed verdict stands. Those two
answers look contradictory and this is the agreed reading of them.
"""

from __future__ import annotations

AUTONOMY_ASSISTED = "assisted"
AUTONOMY_AUTONOMOUS = "autonomous"
VALID_AUTONOMY: tuple[str, ...] = (AUTONOMY_ASSISTED, AUTONOMY_AUTONOMOUS)

# Below this, the gate marks the row. It is not a threshold that changes behaviour: a low-confidence
# binding is still used, because the alternative is discarding the model's best answer in favour of
# no answer. It is a reading aid, pointing a scientist at the rows worth their attention first.
LOW_CONFIDENCE = 0.7


def decision_list(targets: list[dict]) -> list[dict]:
    """The AI decisions behind a plan, one row per claim, for the C1 gate to render.

    Rendered in BOTH modes. An AI decision that cannot be attributed is a defect rather than a
    feature, and this is what makes the "informational only" framing of the output honest: the
    scientist can see which model decided what, on what reasoning, and how sure it was.

    A row the alias table resolved is shown as the alias table's, not the model's. Presenting a
    lookup as a judgment would be the same defect in the other direction.
    """
    rows = []
    for t in targets or []:
        decided_by = t.get("bound_by") or "alias_table"
        by_model = decided_by == "model"
        confidence = t.get("binding_confidence") if by_model else None
        rows.append(
            {
                "metric_key": t.get("metric_key"),
                "bound_key": t.get("bound_key"),
                "resolved": bool(t.get("bound_key")),
                "reason": t.get("binding_reason"),
                "confidence": confidence,
                "model": t.get("bound_by_model") if by_model else None,
                "decided_by": decided_by,
                "low_confidence": bool(by_model and confidence is not None and confidence < LOW_CONFIDENCE),
            }
        )
    return rows
