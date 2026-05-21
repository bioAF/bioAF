"""Tests for the section catalog and prompt builder.

Verifies:
- All sub-item ids are unique across the catalog.
- All sub-items have non-empty labels and prompt fragments.
- default_sub_item_ids respects experiment_only sections.
- assemble_prompt orders sections per the catalog, omits sections with zero
  selected sub-items, drops experiment-only sub-items in Button A scope, and
  includes the response-format schema + template version footer.
- Empty selection raises EmptySectionSelection (prompt body would be empty).
- Unknown sub-item ids raise ValueError.
"""

from __future__ import annotations

import pytest

from app.services.agent_review_prompt_builder import (
    EXPERIMENT_RUN_COMPARISON_V2_BUILDER_NAME,
    PIPELINE_RUN_REVIEW_V2_BUILDER_NAME,
    EmptySectionSelection,
    assemble_prompt,
    template_name_for_scope,
)
from app.services.agent_review_section_catalog import (
    SECTIONS,
    all_sub_items,
    default_sub_item_ids,
    section_for_sub_item,
)


def test_sub_item_ids_unique_across_catalog():
    catalog = all_sub_items()
    flat = [si.id for sec in SECTIONS for si in sec.sub_items]
    assert len(flat) == len(catalog), "duplicate sub-item id in catalog"


def test_literature_results_consistency_subitem_default_on_both_scopes():
    """The literature-vs-results check is a default-on sub-item available to
    both the pipeline-run (Button A) and experiment (Button B) reviews."""
    catalog = all_sub_items()
    assert "literature.results_consistency" in catalog
    si = catalog["literature.results_consistency"]
    assert si.default_on is True
    # Available (default-on) in both scopes: the section is not experiment_only.
    assert "literature.results_consistency" in default_sub_item_ids(experiment_scope=False)
    assert "literature.results_consistency" in default_sub_item_ids(experiment_scope=True)


def test_literature_subitem_fragment_directs_flagging_and_page_citation():
    prompt = assemble_prompt(
        experiment_scope=False,
        selected_sub_item_ids=["literature.results_consistency"],
    )
    lowered = prompt.lower()
    assert "unexpected" in lowered
    assert "contradict" in lowered
    assert "cite" in lowered
    assert "page" in lowered


def test_every_subitem_has_label_and_fragment():
    for sec in SECTIONS:
        for si in sec.sub_items:
            assert si.label, f"empty label for {si.id}"
            assert len(si.prompt_fragment) > 20, f"prompt fragment too short for {si.id}"


def test_section_for_sub_item_finds_parent():
    sec = section_for_sub_item("qc.outlier_detection")
    assert sec is not None and sec.id == "qc"
    assert section_for_sub_item("does.not.exist") is None


def test_default_sub_item_ids_excludes_experiment_only_for_pipeline_scope():
    pipeline_defaults = default_sub_item_ids(experiment_scope=False)
    experiment_defaults = default_sub_item_ids(experiment_scope=True)
    # Cross-sample IDs only appear in the experiment-scope defaults.
    assert any(s.startswith("xsample.") for s in experiment_defaults)
    assert not any(s.startswith("xsample.") for s in pipeline_defaults)


def test_assemble_prompt_for_pipeline_run_scope():
    ids = ["qc.metric_review", "interp.concerns_recommendations"]
    body = assemble_prompt(experiment_scope=False, selected_sub_item_ids=ids)
    assert "single pipeline run output" in body
    assert "## Quality control and technical assessment" in body
    assert "## Interpretation and Recommendations" in body
    # Sections without selections are omitted.
    assert "## Sample metadata patterns" not in body
    # Experiment-only section is never present in Button A.
    assert "## Cross-sample" not in body
    # The response-format schema and template-version footer ship in every prompt.
    assert "JSON header schema" in body
    assert PIPELINE_RUN_REVIEW_V2_BUILDER_NAME in body


def test_assemble_prompt_for_experiment_scope_includes_xsample():
    ids = ["qc.metric_review", "xsample.drift_over_time"]
    body = assemble_prompt(experiment_scope=True, selected_sub_item_ids=ids)
    assert "set of pipeline runs from a single experiment" in body
    assert "## Cross-sample / experiment-level trends" in body
    assert EXPERIMENT_RUN_COMPARISON_V2_BUILDER_NAME in body


def test_assemble_prompt_drops_xsample_ids_in_pipeline_scope_silently():
    """If experiment-only ids leak into a Button A request, drop them silently
    rather than raise; the section header just won't appear."""
    ids = ["qc.metric_review", "xsample.drift_over_time"]
    body = assemble_prompt(experiment_scope=False, selected_sub_item_ids=ids)
    assert "## Quality control and technical assessment" in body
    assert "## Cross-sample" not in body


def test_assemble_prompt_empty_selection_raises():
    with pytest.raises(EmptySectionSelection):
        assemble_prompt(experiment_scope=False, selected_sub_item_ids=[])


def test_assemble_prompt_unknown_subitem_raises():
    with pytest.raises(ValueError):
        assemble_prompt(
            experiment_scope=False,
            selected_sub_item_ids=["does.not.exist"],
        )


def test_template_name_for_scope():
    assert template_name_for_scope(False) == PIPELINE_RUN_REVIEW_V2_BUILDER_NAME
    assert template_name_for_scope(True) == EXPERIMENT_RUN_COMPARISON_V2_BUILDER_NAME


def test_sections_render_in_catalog_order():
    """Assemble with one sub-item per section (where possible) and confirm the
    section headers appear in the same order as SECTIONS."""
    ids: list[str] = []
    for sec in SECTIONS:
        if sec.experiment_only:
            continue
        ids.append(sec.sub_items[0].id)
    body = assemble_prompt(experiment_scope=False, selected_sub_item_ids=ids)
    indexes = []
    for sec in SECTIONS:
        if sec.experiment_only:
            continue
        marker = f"## {sec.label}"
        assert marker in body
        indexes.append(body.index(marker))
    assert indexes == sorted(indexes), "sections out of catalog order"
