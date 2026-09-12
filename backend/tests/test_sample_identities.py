"""change_7.5 section 3.3 (7.4 section 2.3 carried forward): biological units, not columns.

Recording clone, technical replicate and batch is useful only if those fields change what runs;
otherwise two technical measurements of one biological sample satisfy the replicate floor. Each column
carries a biological unit, a biological sample and a technical group, all from evidence. Only a
confirmed technical group is ever collapsed; replicates are counted as distinct biological units; the
selected contrast alone is validated; the pairing check runs after the rewrite; and a pick matching
several columns is a duplicate, never added under one pairing label.
"""

import json
import pathlib

import pytest

from app.services.deposit_metadata_association import rewrite_design_to_columns
from app.services.reproduction_plan_service import validate_paired_designs, validate_replicates
from app.services.validation_input_choice import design_from_mapping

_TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "notebook_templates"


def _row(column, arm, unit, *, group=None, time="day 0"):
    return {"column": column, "arm": arm, "biological_unit": unit, "biological_sample": f"{unit} {arm} {time}",
            "technical_group": group, "time_point": time, "evidence": [{"source": "sample_record", "quote": "x"}]}


_DESIGN = {"contrasts": [{"name": "treated vs control", "test_condition": "treated", "reference_condition": "control",
                          "test_samples": [], "reference_samples": []}]}


# ---- the replicate floor counts distinct biological units ----


def test_an_arm_whose_columns_all_come_from_one_unit_fails_the_floor_whatever_the_column_count():
    design = {"contrasts": [{"name": "c", "test_samples": ["a", "b", "c"], "reference_samples": ["d", "e"],
                             "units": {"a": "D1", "b": "D1", "c": "D1", "d": "D2", "e": "D3"}}]}
    errors = validate_replicates(design)
    assert len(errors) == 1
    assert "test arm" in errors[0] and "1 biological unit" in errors[0]


def test_without_units_the_floor_still_counts_columns():
    design = {"contrasts": [{"name": "c", "test_samples": ["a"], "reference_samples": ["d", "e"]}]}
    assert len(validate_replicates(design)) == 1


# ---- the design is rewritten from the validated mapping ----


def test_two_donors_under_treatment_and_control_with_technical_duplicates_keep_their_pairs():
    """7.4's own case: eight columns collapse to four observations; both pairs survive in the labels."""
    mapping = [
        _row("D1_T_a", "test", "D1", group="D1T"), _row("D1_T_b", "test", "D1", group="D1T"),
        _row("D2_T_a", "test", "D2", group="D2T"), _row("D2_T_b", "test", "D2", group="D2T"),
        _row("D1_C_a", "reference", "D1", group="D1C"), _row("D1_C_b", "reference", "D1", group="D1C"),
        _row("D2_C_a", "reference", "D2", group="D2C"), _row("D2_C_b", "reference", "D2", group="D2C"),
    ]
    design = {"contrasts": [{**_DESIGN["contrasts"][0], "subjects": {"placeholder": "x"}}]}
    rewritten, status, reason = design_from_mapping(design, mapping, contrast_index=0)
    assert status == "ok", reason
    contrast = rewritten["contrasts"][0]
    assert contrast["test_samples"] == ["D1_T_a", "D1_T_b", "D2_T_a", "D2_T_b"]
    assert contrast["technical_groups"] == {
        "D1_T_a": "D1T", "D1_T_b": "D1T", "D2_T_a": "D2T", "D2_T_b": "D2T",
        "D1_C_a": "D1C", "D1_C_b": "D1C", "D2_C_a": "D2C", "D2_C_b": "D2C",
    }
    assert contrast["subjects"]["D1_T_a"] == "D1" and contrast["subjects"]["D2_C_b"] == "D2"
    assert validate_paired_designs(rewritten) == []
    assert validate_replicates(rewritten) == []


def test_a_technical_group_spanning_two_conditions_is_rejected_and_the_design_holds():
    mapping = [_row("a", "test", "D1", group="g"), _row("b", "reference", "D1", group="g"),
               _row("c", "test", "D2"), _row("d", "reference", "D2")]
    _, status, reason = design_from_mapping(_DESIGN, mapping, contrast_index=0)
    assert status == "unsupported"
    assert "spans" in reason


def test_several_biological_samples_of_one_unit_in_one_arm_are_not_collapsed_and_hold():
    mapping = [_row("a", "test", "clone 5"), {**_row("b", "test", "clone 5"), "biological_sample": "clone 5 culture 2"},
               _row("c", "reference", "WT1"), _row("d", "reference", "WT2")]
    _, status, reason = design_from_mapping(_DESIGN, mapping, contrast_index=0)
    assert status == "unsupported"
    assert "one unit" in reason


def test_a_paired_design_whose_names_change_during_mapping_keeps_its_pairing():
    design = {"contrasts": [{**_DESIGN["contrasts"][0], "test_samples": ["GSM1", "GSM2"],
                             "reference_samples": ["GSM3", "GSM4"],
                             "subjects": {"GSM1": "donorA", "GSM2": "donorB", "GSM3": "donorA", "GSM4": "donorB"}}]}
    mapping = [_row("A_treated", "test", "donorA"), _row("B_treated", "test", "donorB"),
               _row("A_control", "reference", "donorA"), _row("B_control", "reference", "donorB")]
    rewritten, status, _ = design_from_mapping(design, mapping, contrast_index=0)
    assert status == "ok"
    assert rewritten["contrasts"][0]["subjects"] == {
        "A_treated": "donorA", "B_treated": "donorB", "A_control": "donorA", "B_control": "donorB"}


def test_a_unit_confounded_with_the_condition_is_caught_after_the_rewrite():
    design = {"contrasts": [{**_DESIGN["contrasts"][0], "subjects": {"x": "y"}}]}
    mapping = [_row("a", "test", "D1"), _row("b", "test", "D2"), _row("c", "reference", "D3"), _row("d", "reference", "D4")]
    _, status, reason = design_from_mapping(design, mapping, contrast_index=0)
    assert status == "pairing_lost"
    assert "pair" in reason


def test_only_the_selected_contrast_is_rewritten():
    design = {"contrasts": [dict(_DESIGN["contrasts"][0]), {"name": "day 7", "test_samples": ["x"], "reference_samples": []}]}
    mapping = [_row("a", "test", "D1"), _row("b", "test", "D2"), _row("c", "reference", "D3"), _row("d", "reference", "D4")]
    rewritten, status, _ = design_from_mapping(design, mapping, contrast_index=0)
    assert status == "ok"
    assert rewritten["contrasts"][1] == design["contrasts"][1]


# ---- the duplicate-pick fix ----


def test_a_pick_matching_several_columns_is_a_duplicate_never_added_under_one_pairing_label():
    design = {"contrasts": [{"name": "c", "test_samples": ["GSM1", "GSM2"], "reference_samples": ["GSM3", "GSM4"],
                             "subjects": {"GSM1": "A", "GSM2": "B", "GSM3": "A", "GSM4": "B"}}]}
    associations = [
        {"column": "s1a", "accessions": ["GSM1"]}, {"column": "s1b", "accessions": ["GSM1"]},
        {"column": "s2", "accessions": ["GSM2"]}, {"column": "s3", "accessions": ["GSM3"]},
        {"column": "s4", "accessions": ["GSM4"]},
    ]
    _, status, reason = rewrite_design_to_columns(design, associations, contrast_index=0)
    assert status == "duplicate"
    assert "GSM1" in reason and "s1a" in reason and "s1b" in reason


# ---- the templates collapse technical groups, and only them ----


def _source(name):
    notebook = json.loads((_TEMPLATES / name).read_text())
    return "\n".join("".join(cell["source"]) for cell in notebook["cells"])


@pytest.mark.parametrize("name,rule", [("de_bulk_deseq2.ipynb", "rowSums"), ("da_peaks_deseq2.ipynb", "rowSums"),
                                       ("de_normalized_limma.ipynb", "rowMeans")])
def test_the_deposit_templates_collapse_by_technical_group_alone(name, rule):
    source = _source(name)
    assert 'technical_group_labels <- ""' in source
    assert f"{rule}(" in source
    # The collapse happens before the model is fitted.
    assert source.rindex("technical_group_labels") < source.index("DESeqDataSetFromMatrix(" if "deseq2" in name else "lmFit(")


def test_the_limma_template_averages_on_the_stored_scale_before_any_log():
    source = _source("de_normalized_limma.ipynb")
    assert source.index("rowMeans(") < source.index("log2(vals + 1)")


def test_the_builtin_registry_declares_the_parameter():
    from app.services.template_notebook_service import BUILTIN_TEMPLATES

    for template in BUILTIN_TEMPLATES:
        if template["local_file"] in ("de_bulk_deseq2.ipynb", "da_peaks_deseq2.ipynb", "de_normalized_limma.ipynb"):
            assert template["parameters"]["technical_group_labels"] == ""


@pytest.mark.parametrize("name", ["de_bulk_deseq2.ipynb", "da_peaks_deseq2.ipynb", "de_normalized_limma.ipynb",
                                  "de_pseudobulk_deseq2.ipynb"])
def test_no_template_code_cell_carries_a_stray_escape_line(name):
    """A line holding only a literal backslash-n is not R; it sat at the end of the limma cell."""
    notebook = json.loads((_TEMPLATES / name).read_text())
    for cell in notebook["cells"]:
        tags = (cell.get("metadata") or {}).get("tags") or []
        if cell["cell_type"] == "code" and "parameters" not in tags:
            assert "\\n" not in [line.strip() for line in "".join(cell["source"]).splitlines()]
