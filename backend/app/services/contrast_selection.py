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

change_7.4 section 1.5: **every selected contrast passes the same checks, including a sole one.**
Study 37 ran nf-core/chipseq on a paper whose contrasts were both RNA-seq; one contrast makes a choice
unambiguous and does not make it compatible. Where the contrast states its assay, that assay is
mapped through the same markers the workflow was chosen with and compared with the workflow and, when
known, with the input's library strategy. A contrast that states no assay is never auto-selected.
"""

from __future__ import annotations

import logging

from app.services.llm_decision import confidence_of, decide, fenced_json

logger = logging.getLogger("bioaf.contrast_selection")

# The step in the user's language, for the issues section of the report.
CONTRAST_SELECTION_INTENT = "choosing which of the paper's contrasts this run reproduces"

# What the deterministic check establishes about one contrast and one route.
COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
# The contrast's assay is not stated, names no workflow, or names more than one. Not established
# either way, so the selector (a model, or a person at the gate) decides.
UNSTATED = "unstated"
# A null selection, recorded as what it means for the route.
NO_COMPATIBLE_CONTRAST = "no_compatible_contrast"


def contrast_compatibility(
    contrast: dict, *, pipeline_key: str | None, library_strategy: str | None = None
) -> tuple[str, str]:
    """``(status, reason)``: whether this route can analyze this contrast, as far as the evidence says."""
    from app.services.pipeline_mapper import pipelines_named_by, route_for_library_strategy, same_route_family
    from app.services.validation_level3_service import supported_finding_kinds

    assay = str(contrast.get("assay") or "").strip()
    if not assay:
        return UNSTATED, "the contrast does not state the assay it was measured on"
    named = pipelines_named_by(assay)
    if not named:
        return UNSTATED, f"bioAF maps the contrast's assay ({assay}) to no workflow"
    if len(named) > 1:
        return UNSTATED, f"the contrast's assay ({assay}) names more than one workflow"
    measured = named[0]
    workflow = pipeline_key or "this route"
    if pipeline_key and not same_route_family(measured, pipeline_key):
        return INCOMPATIBLE, f"it was measured by {assay}, which {workflow} does not analyze"
    strategy = route_for_library_strategy(library_strategy)
    if strategy is not None and measured not in strategy.compatible:
        return INCOMPATIBLE, f"it was measured by {assay}, and the input is {strategy.strategy} data"
    measured_kinds, route_kinds = set(supported_finding_kinds(measured)), set(supported_finding_kinds(pipeline_key))
    if measured_kinds and route_kinds and not measured_kinds & route_kinds:
        kind = sorted(measured_kinds)[0]
        return INCOMPATIBLE, f"it is a {kind} comparison, which {workflow} cannot analyze"
    return COMPATIBLE, f"it was measured by {assay}, which {workflow} analyzes"


def selected_contrast_for(
    design: dict | None, *, pipeline_key: str | None, library_strategy: str | None = None
) -> tuple[int | None, str | None]:
    """``(index, None)`` for the contrast this run analyzes, or ``(None, reason)``.

    change_7.4 sections 1.4 and 1.5: the only way any later step learns which contrast to validate
    and execute. Both analysis routes executed ``contrasts[0]`` whatever was selected, and a null
    selection stopped nothing. Nothing may pick a contrast by position, and a selection that fails
    the check is refused here as well as where it was made, because the pipeline or the design can
    change at the gate after the selection was recorded.
    """
    contrasts = [c for c in (design or {}).get("contrasts") or [] if isinstance(c, dict)]
    if not contrasts:
        return None, "the plan declares no differential contrast to reproduce"
    record = (design or {}).get("selected_contrast")
    if not isinstance(record, dict):
        return None, f"the paper reports {len(contrasts)} contrast(s) and none was selected for this run"
    index = record.get("contrast_index")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(contrasts):
        return None, str(record.get("reason") or "no contrast was selected for this run")
    status, why = contrast_compatibility(contrasts[index], pipeline_key=pipeline_key, library_strategy=library_strategy)
    if status == INCOMPATIBLE:
        return None, f"the selected contrast ({contrasts[index].get('name') or index}) cannot be analyzed here: {why}"
    return index, None


def _none_compatible(contrasts: list[dict], checks: list[tuple[str, str]]) -> dict:
    """A null selection every check agrees on, made without asking anyone."""
    if len(contrasts) == 1:
        reason = f"the paper reports one contrast, and {checks[0][1]}"
    else:
        reason = "; ".join(
            f"{c.get('name') or f'contrast {i}'}: {why}" for i, (c, (_, why)) in enumerate(zip(contrasts, checks))
        )
        reason = f"none of the paper's {len(contrasts)} contrasts can be analyzed on this route ({reason})"
    return {
        "contrast_index": None,
        "reason": reason,
        "confidence": 1.0,
        "decided_by": "compatibility_check",
        "model": None,
        "outcome": NO_COMPATIBLE_CONTRAST,
    }


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
    library_strategy: str | None = None,
    ask: bool = True,
) -> dict | None:
    """The contrast this run reproduces, or None when there is nothing to pick or the ask failed.

    A provider failure returns None rather than falling back to the first contrast: defaulting to
    ``contrasts[0]`` is precisely the defect this replaces, and doing it on an outage would put it
    back exactly where it is hardest to notice.

    change_7.4 section 1.5: the deterministic check runs first, on every contrast. Where it settles
    the answer (every contrast incompatible, or one contrast whose stated assay is compatible) no one
    is asked. Otherwise the selector is asked, even for a sole contrast, and its pick is checked. With
    ``ask=False`` (assisted mode) only a settled answer is returned; a person chooses the rest.
    """
    if not contrasts:
        return None
    checks = [
        contrast_compatibility(c, pipeline_key=pipeline_key, library_strategy=library_strategy) for c in contrasts
    ]
    if all(status == INCOMPATIBLE for status, _ in checks):
        return _none_compatible(contrasts, checks)
    if len(contrasts) == 1 and checks[0][0] == COMPATIBLE:
        return {
            "contrast_index": 0,
            "reason": f"the paper reports one contrast, and {checks[0][1]}",
            "confidence": 1.0,
            "decided_by": "only_contrast",
            "model": None,
        }
    if not ask:
        return None

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
    index = selected["contrast_index"]
    if index is None:
        return {**selected, "decided_by": "model", "model": model, "outcome": NO_COMPATIBLE_CONTRAST}
    status, why = checks[index]
    if status == INCOMPATIBLE:
        # The model's pick fails the same check a sole contrast does. Recorded with its choice, so
        # the report can say what was proposed and why it was refused.
        return {
            "contrast_index": None,
            "reason": f"the model chose {contrasts[index].get('name') or f'contrast {index}'}, but {why}",
            "confidence": selected["confidence"],
            "decided_by": "compatibility_check",
            "model": model,
            "model_choice": index,
            "outcome": NO_COMPATIBLE_CONTRAST,
        }
    return {**selected, "decided_by": "model", "model": model}
