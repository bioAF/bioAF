"""change_7.5 section 4.1 (7.4 section 3.1 carried forward): consistency with the authors' results.

The authors' own result table is checked against the claim at the claim's predicate: the significance
column by kind, the stated operators, the direction and the count relation. A table's conventions
(the effect scale, the ratio orientation, and for a headerless table each column's role) need evidence;
numbers alone never establish them, and a check that depends on an unestablished convention is
unresolved with the candidate interpretation shown. Consistency is not execution.

Synthetic tables only: no value from a fixture paper reaches this code.
"""

from app.services.validation_author_consistency import check_claim, read_table
from app.services.validation_predicate import build_predicate

_P = {"kind": "pvalue", "operator": "<", "value": 0.01}
_CONTRAST = {"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT"}


def _claim(value, direction, **over):
    return {"claim_text": f"{value} genes were {direction}", "claimed_value": value, "output_type": "gene_set_size",
            "direction": direction, "contrast_index": 0, "cutoffs": [_P], **over}


def _check(claim, text, **kw):
    predicate = build_predicate(claim, contrast=_CONTRAST)
    return check_claim(claim, predicate, {"name": "t.txt", "text": text, "source": "deposit"}, contrast=_CONTRAST, **kw)


_ORIENTED = (
    "gene\tlog2FoldChange(KO/WT)\tpvalue\tpadj\n"
    "g1\t1.2\t0.001\t0.2\n"
    "g2\t2.0\t0.004\t0.3\n"
    "g3\t-1.5\t0.002\t0.2\n"
    "g4\t0.8\t0.02\t0.4\n"
    "g5\tNA\tNA\tNA\n"
)


def test_the_predicate_is_applied_to_the_table_and_the_count_relation_decides():
    record = _check(_claim(2, "up"), _ORIENTED)
    assert record["outcome"] == "agree"
    assert record["rows_tested"] == 5
    assert record["rows_passing"] == 2
    assert record["rows_missing"] == 1
    assert record["columns"] == {"id": "gene", "lfc": "log2FoldChange(KO/WT)", "pvalue": "pvalue", "padj": "padj"}
    assert record["claim"] == {"value": 2.0, "relation": "="}
    assert record["counting"] == {"entity": "gene", "dedup": "distinct_identifier", "missing": "exclude_and_report"}
    assert _check(_claim(3, "up"), _ORIENTED)["outcome"] == "disagree"


def test_a_p_value_claim_is_never_read_from_the_adjusted_column():
    record = _check(_claim(2, "up"), "gene\tlog2FoldChange(KO/WT)\tpadj\ng1\t1.2\t0.001\n")
    assert record["outcome"] == "not_checkable"
    assert "P-value column" in record["reason"]


def test_orientation_needs_evidence_and_the_arm_names_of_the_contrast_never_supply_it():
    table = _ORIENTED.replace("log2FoldChange(KO/WT)", "log2FoldChange")
    record = _check(_claim(2, "up"), table)
    assert record["outcome"] == "unresolved"
    assert "orientation" in record["reason"]
    # Both candidate interpretations are shown.
    assert record["candidates"] == [
        {"interpretation": "the table is KO over WT", "count": 2},
        {"interpretation": "the table is WT over KO", "count": 1},
    ]


def test_a_check_that_does_not_depend_on_orientation_proceeds():
    table = _ORIENTED.replace("log2FoldChange(KO/WT)", "log2FoldChange")
    record = _check(_claim(3, None), table)
    assert record["outcome"] == "agree"


def test_a_recorded_confirmation_establishes_orientation_and_is_reported_as_an_assumption():
    table = _ORIENTED.replace("log2FoldChange(KO/WT)", "log2FoldChange")
    confirmation = {"orientation": "test_over_reference", "confirmed_by": "a person", "at": "2026-09-12"}
    record = _check(_claim(2, "up"), table, interpretation=confirmation)
    assert record["outcome"] == "agree"
    assert any("confirmed" in a for a in record["assumptions"])


def test_a_headerless_table_is_unresolved_with_candidate_roles_and_numbers_never_establish_them():
    headerless = "g1\t1.2\t0.001\t0.2\ng2\t-2.0\t0.004\t0.3\ng3\t0.5\t0.5\t0.9\n"
    record = _check(_claim(1, "up"), headerless)
    assert record["outcome"] == "unresolved"
    assert "headerless" in record["reason"]
    roles = record["candidate_roles"]
    assert roles["id"] == [0]
    assert 1 in roles["lfc"]  # negative values: can be a log fold change
    assert 1 not in roles["pvalue"]  # values outside 0..1 rule it out as a P value


def test_a_confirmed_interpretation_of_a_headerless_table_produces_the_count():
    headerless = "g1\t1.2\t0.001\t0.2\ng2\t-2.0\t0.004\t0.3\ng3\t0.5\t0.5\t0.9\n"
    confirmation = {"columns": {"id": 0, "lfc": 1, "pvalue": 2}, "orientation": "test_over_reference",
                    "effect_scale": "log2", "confirmed_by": "a person", "at": "2026-09-12"}
    record = _check(_claim(1, "up"), headerless, interpretation=confirmation)
    assert record["outcome"] == "agree"
    assert record["rows_passing"] == 1


def test_duplicates_that_disagree_straddling_the_claim_are_unresolved():
    table = "gene\tlog2FoldChange(KO/WT)\tpvalue\ng1\t1.2\t0.001\ng1\t1.2\t0.5\ng2\t2.0\t0.004\n"
    record = _check(_claim(2, "up"), table)
    assert record["count_range"] == [1, 2]
    assert record["duplicates_disagreeing"] == ["g1"]
    assert record["outcome"] == "unresolved"


def test_a_linear_fold_change_column_with_negative_values_is_ruled_out():
    table = "gene\tFoldChange\tpvalue\ng1\t-1.2\t0.001\n"
    claim = _claim(1, None, cutoffs=[_P, {"kind": "fold_change", "operator": ">", "value": 2}])
    record = _check(claim, table)
    assert record["outcome"] == "unresolved"
    assert "scale" in record["reason"]


def test_the_table_reader_finds_the_contrasts_own_columns_in_a_wide_table():
    table = "gene\tKO_vs_WT_log2FC\tKO_vs_WT_pvalue\tDay7_vs_WT_log2FC\tDay7_vs_WT_pvalue\ng1\t1.0\t0.001\t3.0\t0.5\n"
    reading = read_table(table, contrast_name="KO_vs_WT")
    assert reading["columns"]["lfc"] == "KO_vs_WT_log2FC"
    assert reading["columns"]["pvalue"] == "KO_vs_WT_pvalue"


# ---- wiring: supplements, the acquired authors' table, the report ----


def test_a_results_supplement_is_checked_for_each_claim_and_keeps_no_rows():
    from app.services.validation_author_consistency import supplement_consistency

    claim = _claim(2, "up")
    predicate = build_predicate(claim, contrast=_CONTRAST)
    records = supplement_consistency(
        _ORIENTED.encode(), "s3.txt", [{"claim_index": 4, "predicate": predicate, "contrast": _CONTRAST}]
    )
    [record] = records
    assert record["claim_index"] == 4
    assert record["outcome"] == "agree"
    assert record["source"] == "supplement"
    assert "rows" not in record


def test_the_report_carries_each_claims_consistency_and_the_level_3_result_attached_to_it():
    from app.services.validation_report_summary import summarize

    claim = _claim(2, "up")
    predicate = build_predicate(claim, contrast=_CONTRAST)
    record = {**check_claim(claim, predicate, {"name": "t.txt", "text": _ORIENTED, "source": "deposit"}, contrast=_CONTRAST),
              "claim_index": 0}
    evidence = {
        "author_consistency": {"records": [record]},
        "level3": {"claim_index": 0, "source": "deposit", "contrast": "KO vs WT"},
        "level3_result": {"concordance": {"verdict": "reproduced"},
                          "claim_count": {"count": 2, "status": "holds", "words": "2 against the claim's exactly 2",
                                          "label": "Reanalysis count, from a different method than the paper's"}},
    }
    plan = {"differential_design": {"contrasts": [_CONTRAST]}}
    summary = summarize(study={"state": "comparing"}, evidence=evidence, plan=plan, targets=[claim], issues=[])
    row = summary["claims"][0]
    assert row["consistency"]["outcome"] == "agree"
    assert row["consistency"]["label"] == "Consistent with the authors' deposited results"
    assert row["consistency"]["table"] == "t.txt"
    assert row["result"]["tier"] == "Deposited data"
    assert row["result"]["count"]["words"] == "2 against the claim's exactly 2"
    assert "different method" in row["result"]["count"]["label"]


def test_a_single_results_supplement_answers_the_claims_it_was_checked_for():
    from app.services.validation_report_summary import summarize

    claim = _claim(2, "up")
    predicate = build_predicate(claim, contrast=_CONTRAST)
    record = {**check_claim(claim, predicate, {"name": "s3.txt", "text": _ORIENTED, "source": "supplement"},
                            contrast=_CONTRAST), "claim_index": 0}
    evidence = {"supplements": [{"label": "S3", "filename": "s3.txt", "role": "results_table", "resolved": True,
                                 "consistency": [record]}]}
    plan = {"differential_design": {"contrasts": [_CONTRAST]}}
    summary = summarize(study={"state": "classified"}, evidence=evidence, plan=plan, targets=[claim], issues=[])
    assert summary["claims"][0]["consistency"]["table"] == "s3.txt"
    # Consistency is not execution: the headline stays with the attempt.
    assert summary["attempt"]["status"] == "not_attempted"


def test_the_markdown_renders_each_claim_from_the_projection():
    from app.services.provenance.markdown_renderer import _append_each_claim
    from app.services.validation_report_summary import summarize

    claim = {**_claim(2, "up"), "checks": {"author_results": {"status": "available", "reason": "t.txt", "requirement": None}}}
    predicate = build_predicate(claim, contrast=_CONTRAST)
    record = {**check_claim(claim, predicate, {"name": "t.txt", "text": _ORIENTED, "source": "deposit"}, contrast=_CONTRAST),
              "claim_index": 0}
    plan = {"differential_design": {"contrasts": [_CONTRAST]}}
    summary = summarize(study={"state": "comparing"}, evidence={"author_consistency": {"records": [record]}}, plan=plan,
                        targets=[claim], issues=[])
    parts: list[str] = []
    _append_each_claim(parts, summary)
    text = "\n".join(parts)
    assert "## Each claim" in text
    assert "KO versus WT, P < 0.01, up, no fold-change requirement" in text
    assert "Consistency with the authors' results: Available" in text
    assert "Consistent with the authors' deposited results" in text
