"""Standardized prompt templates for agent reviews (ADR-052, ADR-053).

Two templates are supported in v1:
    pipeline_run_review_v1     -- Button A: single-run review.
    experiment_run_comparison_v1 -- Button B: cross-run review.

The version string in the template name is also persisted on the agent_review
and agent_review_job rows so we can trace which template produced any past
card.
"""

from __future__ import annotations

from app.exceptions import ValidationError

PIPELINE_RUN_REVIEW_V1_NAME = "pipeline_run_review_v1"
EXPERIMENT_RUN_COMPARISON_V1_NAME = "experiment_run_comparison_v1"


PIPELINE_RUN_REVIEW_V1 = """\
You are a research-assistant scientist reviewing a single pipeline run output.

You will read the attached Markdown summary of the run (and optionally an HTML pipeline report).

Your job:
1. Flag anomalies, unexpected QC results, parameter values that seem inconsistent with the run's stated intent, or any pattern in the sample data that a scientist should know about.
2. If everything looks fine, say so explicitly.
3. Do NOT perform statistical analysis. Do NOT recompute values. You are a reviewer, not an analyst.

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

Template version: pipeline_run_review_v1
"""


EXPERIMENT_RUN_COMPARISON_V1 = """\
You are a research-assistant scientist reviewing a set of pipeline runs from a single experiment.

You will read N attached Markdown summaries (one per pipeline run) plus an experiment header.

Your job:
1. Compare the runs to each other. Surface trends, divergences, sample-level patterns across runs, and parameter or pipeline-version drift that may affect comparability.
2. Note where the runs are not apples-to-apples (different pipeline versions or parameters) so the scientist knows to weight comparisons accordingly.
3. If the runs look consistent and comparable, say so explicitly.
4. Do NOT perform statistical analysis. Do NOT recompute values.

Respond with a single fenced JSON block at the top of your response, followed by a free-text body.

JSON header schema and severity guide: same as pipeline_run_review_v1.

Template version: experiment_run_comparison_v1
"""


_TEMPLATES: dict[str, str] = {
    PIPELINE_RUN_REVIEW_V1_NAME: PIPELINE_RUN_REVIEW_V1,
    EXPERIMENT_RUN_COMPARISON_V1_NAME: EXPERIMENT_RUN_COMPARISON_V1,
}


def get_template(name: str) -> str:
    if name not in _TEMPLATES:
        raise ValidationError(f"unknown prompt template: {name}")
    return _TEMPLATES[name]
