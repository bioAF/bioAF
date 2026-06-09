"""Assemble an LLM review prompt from selected section sub-items.

The skeleton (response-format JSON header schema, severity guide, opening
instructions) is constant per scope; the "what to look for" body is composed
from the catalog sub-items the user selected.
"""

from __future__ import annotations

from app.exceptions import ValidationError
from app.services.agent_review_section_catalog import SECTIONS, all_sub_items

PIPELINE_RUN_REVIEW_V2_BUILDER_NAME = "pipeline_run_review_v2_builder"
EXPERIMENT_RUN_COMPARISON_V2_BUILDER_NAME = "experiment_run_comparison_v2_builder"


_PIPELINE_RUN_SKELETON_HEAD = (
    "You are a research-assistant scientist reviewing a single pipeline run output.\n\n"
    "You will read the attached Markdown summary of the run (and optionally an HTML pipeline report).\n\n"
)


_EXPERIMENT_SKELETON_HEAD = (
    "You are a research-assistant scientist reviewing a set of pipeline runs from a single experiment.\n\n"
    "You will read N attached Markdown summaries (one per pipeline run) plus an experiment header.\n\n"
)


_COMMON_INSTRUCTIONS = (
    "Your job is to provide an advisory review covering the topics below. For each topic, "
    "address what the data supports; skip silently any topic where the data does not allow a "
    "meaningful finding. Do NOT perform statistical analysis. Do NOT recompute values. You are "
    "a reviewer, not an analyst.\n"
)


_RESPONSE_SCHEMA = """
Respond with a single fenced JSON block at the top of your response (the exact schema is below), followed by a free-text body explaining your reasoning in plain English a scientist can scan.

JSON header schema:
{
  "severity": "red" | "orange" | "green",
  "headline": "<one-sentence summary of the most important thing you found>",
  "flags": [
    { "title": "<short>", "body": "<one paragraph>", "severity": "red" | "orange" | "green" }
  ],
  "evidence": [ "<reference to a specific field, sample, or value in the input>" ]
}

Severity guide:
- red: a major concern (likely failure, contamination, parameter mismatch with downstream impact).
- orange: something strange but not a clear failure (outlier sample, unusual QC distribution).
- green: nothing flagged; the run looks consistent with expectations.

Begin your response with the JSON block, then the free-text body.
"""


class EmptySectionSelection(ValidationError):
    """Raised when no sub-items are selected; the prompt body would be empty."""


def assemble_prompt(
    *,
    experiment_scope: bool,
    selected_sub_item_ids: list[str],
) -> str:
    """Build the assembled prompt for the given scope and selection.

    Sub-items are grouped under their parent section, sections appear in the
    catalog's defined order, and sections with zero selected sub-items are
    omitted entirely.
    """
    if not selected_sub_item_ids:
        raise EmptySectionSelection("no sub-items selected; the assembled prompt would have no review body")

    catalog = all_sub_items()
    selected_set = set(selected_sub_item_ids)
    unknown = selected_set - catalog.keys()
    if unknown:
        raise ValidationError(f"unknown sub-item ids: {sorted(unknown)}")

    sections_block_parts: list[str] = []
    for sec in SECTIONS:
        if sec.experiment_only and not experiment_scope:
            # Silently drop experiment-only sub-items if any leaked through in
            # a Button A request; do not surface as an error.
            continue
        chosen = [si for si in sec.sub_items if si.id in selected_set]
        if not chosen:
            continue
        sections_block_parts.append(f"## {sec.label}")
        for si in chosen:
            sections_block_parts.append(f"- {si.prompt_fragment}")
        sections_block_parts.append("")

    sections_block = "\n".join(sections_block_parts).rstrip() + "\n"

    head = _EXPERIMENT_SKELETON_HEAD if experiment_scope else _PIPELINE_RUN_SKELETON_HEAD
    version_name = (
        EXPERIMENT_RUN_COMPARISON_V2_BUILDER_NAME if experiment_scope else PIPELINE_RUN_REVIEW_V2_BUILDER_NAME
    )

    return (
        head + _COMMON_INSTRUCTIONS + "\n" + sections_block + _RESPONSE_SCHEMA + f"\nTemplate version: {version_name}\n"
    )


def template_name_for_scope(experiment_scope: bool) -> str:
    return EXPERIMENT_RUN_COMPARISON_V2_BUILDER_NAME if experiment_scope else PIPELINE_RUN_REVIEW_V2_BUILDER_NAME
