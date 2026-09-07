"""Which of a paper's contrasts does THIS run reproduce?

A paper reports one contrast per finding, across every assay it ran. A plan runs ONE pipeline. The
C1 gate used ``contrasts[0]`` and nothing matched the two up, so a multi-assay paper offered
whichever contrast the model happened to list first. GSE273743's paper states six across RNA-seq,
ATAC-seq and ChIP-seq and lists the ChIP-seq one LAST, so a chipseq run was offered an RNA-seq
knockout contrast, and every downstream number would have been measured against the wrong finding.

This cannot be assigned by rule without encoding every way a paper can word an assay ("chromatin
accessibility", "occupancy profiling", "differential binding", "CUT&RUN"), which is the kind of
lookup table plan_6 exists to remove. So it is a semantic decision: the model is given the
contrasts and the pipeline being run, and picks.

Selecting nothing is a real answer. A paper whose findings none of this pipeline can reproduce is a
Level-2 study, and saying so is better than reproducing the wrong contrast confidently.
"""

from __future__ import annotations

import logging

from app.services.llm_decision import confidence_of, decide, fenced_json

logger = logging.getLogger("bioaf.contrast_selection")

# The step in the user's language, for the issues section of the report.
CONTRAST_SELECTION_INTENT = "choosing which of the paper's contrasts this run reproduces"


def build_contrast_prompt(
    contrasts: list[dict],
    *,
    pipeline_key: str | None,
    assay: str | None,
    accession: str | None = None,
    sample_titles: list[str] | None = None,
) -> tuple[str, str]:
    """Return (system, payload) asking which contrast this pipeline's run could reproduce."""
    system = (
        "You are matching a paper's reported findings to one analysis run. The run below executes a "
        "single pipeline on a single dataset, so it can reproduce AT MOST ONE of the paper's "
        "contrasts: the one measured on the same assay, on the same kind of data.\n\n"
        "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
        '{"contrast_index": 0 or null, "reason": "one sentence", "confidence": 0.0 to 1.0}\n\n'
        "Rules:\n"
        "- Choose the contrast this run could actually reproduce, by the assay it was measured on, "
        "not by the order the contrasts are listed in.\n"
        "- SELECTING NOTHING IS A CORRECT ANSWER. Use null when no contrast was measured on this "
        "run's assay. The study is then a quality-control comparison only, which is an honest "
        "result; reproducing the wrong contrast is not.\n"
        "- Match on what was measured. Chromatin accessibility is ATAC, occupancy and differential "
        "binding are ChIP, expression is RNA. A knockout studied by RNA-seq is not a ChIP contrast "
        "even when the same gene is named in both.\n"
        "- When two contrasts were measured on the SAME assay, the pipeline alone cannot separate "
        "them. Use the dataset below: its accession and its SAMPLE titles say which conditions were "
        "actually sequenced here, and only the contrast between those conditions is reproducible "
        "from this data."
    )
    lines = []
    for i, c in enumerate(contrasts):
        lines.append(
            f"  [{i}] {c.get('name') or '(unnamed)'}"
            f" | assay: {c.get('assay') or 'not stated'}"
            f" | {c.get('test_condition') or '?'} vs {c.get('reference_condition') or '?'}"
        )
    dataset = f"This run is scoped to: {accession or 'no specific accession'}\n"
    if sample_titles:
        dataset += "Its samples:\n" + "\n".join(f"  {t}" for t in sample_titles[:40]) + "\n"
    payload = (
        f"This run executes: {pipeline_key or 'unknown pipeline'}\n"
        f"The paper's methods call its assay: {assay or 'not stated'}\n"
        f"{dataset}\n"
        "The paper's contrasts:\n" + "\n".join(lines)
    )
    return system, payload


def parse_contrast_selection(response_text: str, *, n: int) -> dict:
    """Read the selection, refusing an index the contrast list does not have."""
    empty = {"contrast_index": None, "reason": "", "confidence": 0.0}
    data = fenced_json(response_text)
    if data is None:
        return empty

    reason = str(data.get("reason") or "").strip()
    confidence = confidence_of(data.get("confidence"))

    idx = data.get("contrast_index")
    if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx < n):
        # Out of range is not a near-miss to be clamped: a wrong contrast is measured against the
        # wrong finding, so an unusable index selects nothing.
        return {"contrast_index": None, "reason": reason, "confidence": confidence}
    return {"contrast_index": idx, "reason": reason, "confidence": confidence}


async def select_contrast(
    contrasts: list[dict],
    *,
    pipeline_key: str | None,
    assay: str | None,
    client,
    model: str,
    api_key: str | None,
    accession: str | None = None,
    sample_titles: list[str] | None = None,
    on_issue=None,
) -> dict | None:
    """The contrast this run reproduces, or None when there is nothing to pick or the ask failed.

    A provider failure returns None rather than falling back to the first contrast: defaulting to
    ``contrasts[0]`` is precisely the defect this replaces, and doing it on an outage would put it
    back exactly where it is hardest to notice.
    """
    if not contrasts:
        return None
    if len(contrasts) == 1:
        return {
            "contrast_index": 0,
            "reason": "the paper reports one contrast",
            "confidence": 1.0,
            "decided_by": "only_contrast",
            "model": None,
        }

    system, payload = build_contrast_prompt(
        contrasts, pipeline_key=pipeline_key, assay=assay, accession=accession, sample_titles=sample_titles
    )
    decision = await decide(
        intent=CONTRAST_SELECTION_INTENT,
        system=system,
        payload=payload,
        client=client,
        model=model,
        api_key=api_key,
    )
    if not decision.ok:
        # No fallback to contrasts[0]: defaulting to the first one is precisely the defect this
        # replaces, and doing it on an outage would put it back where it is hardest to notice. The
        # gate then shows every contrast for a person, which is `degraded` rather than `blocked`.
        if on_issue:
            on_issue(decision.as_issue(impact="degraded"))
        return None

    selected = parse_contrast_selection(decision.text, n=len(contrasts))
    return {**selected, "decided_by": "model", "model": model}
