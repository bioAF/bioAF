"""change_7.5 section 3.1 (7.4 section 2.4 carried forward): one statistical definition per claim, the
finding predicate, applied by one function.

It preserves a P value versus an adjusted P value, `>` versus `>=`, "more than 5,000" versus "exactly"
versus "about", no fold-change requirement versus an unspecified one. The legacy shapes are read
through one adapter, and nothing new is written in them. Deterministic, no model call.
"""

import pytest

from app.services.validation_predicate import (
    build_predicate,
    count_passing,
    evaluate_count,
    legacy_cutoffs,
    predicate_words,
    row_passes,
)

_P = {"kind": "pvalue", "operator": "<", "value": 0.01}
_CONTRAST = {"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT"}


def _claim(**over):
    return {"claim_text": "524 genes were down (P < 0.01)", "claimed_value": 524, "output_type": "gene_set_size",
            "direction": "down", "contrast_index": 0, "cutoffs": [_P], **over}


# ---- building ----


def test_the_predicate_keeps_the_stated_kind_operator_and_direction():
    predicate = build_predicate(_claim(), contrast=_CONTRAST)
    assert predicate["significance"] == {"kind": "pvalue", "operator": "<", "value": 0.01, "adjustment": None}
    assert predicate["significance_status"] == "resolved"
    assert predicate["direction"] == "down"
    assert predicate["effect"] == {"kind": "none"}  # the claim's cutoffs are its complete statement
    assert predicate["count"]["relation"] == "="
    assert predicate["count"]["value"] == 524
    assert predicate["count"]["entity"] == "gene"
    assert predicate["orientation"] == "test_over_reference"
    assert predicate["status"] == "resolved"


def test_a_nominal_p_value_is_never_unresolved_for_being_nominal():
    assert build_predicate(_claim(), contrast=_CONTRAST)["significance_status"] == "resolved"


def test_significance_is_unresolved_only_through_a_kept_ambiguity():
    predicate = build_predicate(_claim(significance_unresolved="two readings"), contrast=_CONTRAST)
    assert predicate["significance_status"] == "unresolved"
    assert predicate["status"] == "unresolved"


def test_an_adjustment_method_the_paper_states_is_recorded():
    claim = _claim(cutoffs=[{"kind": "padj", "operator": "<", "value": 0.05, "adjustment": "BH"}])
    assert build_predicate(claim, contrast=_CONTRAST)["significance"]["adjustment"] == "BH"
    fdr = _claim(cutoffs=[{"kind": "fdr", "operator": "<", "value": 0.05}])
    assert build_predicate(fdr, contrast=_CONTRAST)["significance"] == {
        "kind": "padj", "operator": "<", "value": 0.05, "adjustment": "FDR unspecified"}


def test_more_than_and_about_are_kept():
    more = build_predicate(_claim(claimed_value=5000, count_relation=">"), contrast=_CONTRAST)
    assert more["count"]["relation"] == ">"
    about = build_predicate(_claim(claimed_value=5000, count_relation="approx", tolerance=0.1), contrast=_CONTRAST)
    assert about["count"]["relation"] == "approx"
    assert about["count"]["tolerance"] == {"kind": "relative", "value": 0.1}


def test_a_bare_count_is_read_as_exact_and_the_reading_is_an_assumption():
    predicate = build_predicate(_claim(), contrast=_CONTRAST)
    assert any("exactly" in a for a in predicate["assumptions"])


def test_an_unspecified_fold_change_is_not_the_same_as_none():
    legacy = {"claim_text": "x", "claimed_value": 10, "output_type": "gene_set_size", "contrast_index": 0}
    predicate = build_predicate(legacy, contrast={"name": "c", "thresholds": {"padj": 0.05, "log2fc": None}},
                                design={"thresholds": {}})
    assert predicate["effect"] is None
    assert predicate["significance"]["kind"] == "padj"


def test_no_stated_significance_is_not_checkable_for_a_count():
    predicate = build_predicate(_claim(cutoffs=[]), contrast=_CONTRAST)
    assert predicate["status"] == "not_checkable"
    assert "significance" in predicate["reason"]


# ---- the legacy shapes through one adapter ----


def test_the_legacy_shapes_are_read_through_one_adapter():
    assert legacy_cutoffs({"cutoffs": [_P]}) == [_P]
    assert legacy_cutoffs({"threshold": 0.05, "threshold_kind": "padj"}) == [{"kind": "padj", "operator": "<", "value": 0.05}]
    assert legacy_cutoffs({}, contrast={"thresholds": {"padj": 0.1, "log2fc": 1.0}}) == [
        {"kind": "padj", "operator": "<=", "value": 0.1},
        {"kind": "abs_log2fc", "operator": ">=", "value": 1.0},
    ]
    assert legacy_cutoffs({}, finding_claim={"thresholds": {"padj": 0.05, "log2fc": 0.5}})[0]["kind"] == "padj"


# ---- one function applies it ----


def test_one_function_decides_a_row_by_kind_operator_effect_and_direction():
    predicate = build_predicate(_claim(), contrast=_CONTRAST)
    assert row_passes(predicate, pvalue=0.005, padj=0.2, lfc=-0.3) is True
    assert row_passes(predicate, pvalue=0.01, padj=0.001, lfc=-2.0) is False  # < is strict
    assert row_passes(predicate, pvalue=0.001, padj=0.001, lfc=1.5) is False  # wrong direction
    assert row_passes(predicate, pvalue=None, padj=0.001, lfc=-1.5) is None  # missing is never 0 or 1


def test_an_effect_requirement_is_applied_on_the_log2_scale():
    claim = _claim(cutoffs=[_P, {"kind": "fold_change", "operator": ">", "value": 2}])
    predicate = build_predicate(claim, contrast=_CONTRAST)
    assert row_passes(predicate, pvalue=0.001, padj=None, lfc=-1.2) is True
    assert row_passes(predicate, pvalue=0.001, padj=None, lfc=-0.8) is False


def test_counting_is_of_distinct_identifiers_with_missing_rows_reported():
    predicate = build_predicate(_claim(), contrast=_CONTRAST)
    rows = [
        {"id": "g1", "pvalue": 0.001, "lfc": -1.0},
        {"id": "g1", "pvalue": 0.002, "lfc": -1.1},  # the same gene twice, both passing
        {"id": "g2", "pvalue": 0.001, "lfc": -1.0},
        {"id": "g3", "pvalue": None, "lfc": -1.0},  # independent filtering
        {"id": "g4", "pvalue": 0.5, "lfc": -1.0},
    ]
    result = count_passing(rows, predicate)
    assert result["count"] == 2
    assert result["count_range"] == [2, 2]
    assert result["rows_missing"] == 1
    assert result["passing"] == ["g1", "g2"]


def test_duplicates_that_disagree_make_the_count_a_range():
    predicate = build_predicate(_claim(), contrast=_CONTRAST)
    rows = [{"id": "g1", "pvalue": 0.001, "lfc": -1.0}, {"id": "g1", "pvalue": 0.5, "lfc": -1.0}]
    result = count_passing(rows, predicate)
    assert result["count_range"] == [0, 1]
    assert result["duplicates_disagreeing"] == ["g1"]


# ---- the count relation ----


@pytest.mark.parametrize(
    "relation,value,observed,expected",
    [("=", 524, [524, 524], "holds"), ("=", 524, [500, 500], "fails"), (">", 5000, [6000, 6000], "holds"),
     (">", 5000, [5000, 5000], "fails"), (">", 5000, [4000, 6000], "unresolved")],
)
def test_the_count_relation_is_honoured(relation, value, observed, expected):
    status, _words = evaluate_count({"relation": relation, "value": value, "tolerance": None}, observed)
    assert status == expected


def test_about_with_no_stated_tolerance_is_unresolved_never_guessed():
    status, words = evaluate_count({"relation": "approx", "value": 5000, "tolerance": None}, [5100, 5100])
    assert status == "unresolved"
    assert "tolerance" in words
    status, _ = evaluate_count({"relation": "approx", "value": 5000, "tolerance": {"kind": "relative", "value": 0.05}}, [5100, 5100])
    assert status == "holds"


# ---- display ----


def test_the_predicate_is_stated_in_words():
    words = predicate_words(build_predicate(_claim(), contrast=_CONTRAST), contrast=_CONTRAST)
    assert words == "KO versus WT, P < 0.01, down, no fold-change requirement"


# ---- wiring: extraction, storage, selection, report, the normalizers ----


def test_the_extraction_asks_for_the_count_relation_and_the_adjustment():
    from app.services.validation_extraction_service import build_extraction_prompt

    system, _ = build_extraction_prompt("text")
    assert '"count_relation": "= | > | >= | < | <= | approx"' in system
    assert '"adjustment": "BH | Bonferroni | q-value | FDR unspecified | null"' in system
    assert "more than 3,000" in system


def test_a_claims_cutoffs_keep_the_adjustment_the_paper_states():
    from app.services.validation_claim_cutoffs import claim_cutoffs

    assert claim_cutoffs({"cutoffs": [{"kind": "fdr", "operator": "<", "value": 0.05}]}) == [
        {"kind": "padj", "operator": "<", "value": 0.05, "adjustment": "FDR unspecified"}
    ]
    assert claim_cutoffs({"cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05, "adjustment": "BH"}]})[0][
        "adjustment"
    ] == "BH"
    assert "adjustment" not in claim_cutoffs({"cutoffs": [_P]})[0]


@pytest.mark.asyncio
async def test_the_count_relation_is_stored_and_validated(session, admin_user):
    from app.services.reproduction_plan_service import ReproductionPlanService
    from app.services.validation_study_service import ValidationStudyService

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, accessions=["GSE1"])
    rows = await ReproductionPlanService.add_comparison_targets(
        session, plan, [{"metric_key": "", "claim_text": "more than 5000 genes", "claimed_value": 5000.0, "count_relation": ">"},
                        {"metric_key": "", "claim_text": "roughly one", "claimed_value": 1.0, "count_relation": "roughly"}],
    )
    assert [r.count_relation for r in rows] == [">", None]


@pytest.mark.asyncio
async def test_the_selection_carries_the_selected_claims_predicate():
    from app.services.validation_selection import select_analysis

    target = {**_claim(), "reported_experiment_id": "e1", "bound_by": "model"}
    checks = [{"processed_reanalysis": {"status": "unresolved", "requirement": "sample_mapping"}}]
    selection = await select_analysis(
        [target], checks, experiments=[{"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"}],
        contrasts=[{**_CONTRAST, "assay": "bulk RNA-seq", "cutoffs": [_P]}], route="deposit", autonomous=False,
        client=None, model="m", api_key=None,
    )
    predicate = selection["current"]["predicate"]
    assert predicate["significance"]["kind"] == "pvalue"
    assert predicate["direction"] == "down"
    assert selection["current"]["predicate_words"] == "KO versus WT, P < 0.01, down, no fold-change requirement"


def test_the_report_states_each_claims_predicate_in_words():
    from app.services.validation_report_summary import summarize

    plan = {"differential_design": {"contrasts": [_CONTRAST]}}
    summary = summarize(study={"state": "plan_ready"}, evidence={}, plan=plan, targets=[_claim()], issues=[])
    assert summary["claims"][0]["predicate"] == "KO versus WT, P < 0.01, down, no fold-change requirement"


def test_the_normalizer_filters_through_the_one_function(monkeypatch):
    from app.services import result_set_normalizer, validation_predicate

    calls = []
    real = validation_predicate.row_passes

    def counted(predicate, **kw):
        calls.append(predicate)
        return real(predicate, **kw)

    monkeypatch.setattr(validation_predicate, "row_passes", counted)
    table = "gene\tlog2FoldChange\tpvalue\tpadj\ng1\t-1.5\t0.001\t0.01\ng2\t0.2\t0.5\t0.9\n"
    fs = result_set_normalizer.normalize_gene_table(
        table, padj_threshold=0.01, significance_kind="pvalue", significance_operator="<", lfc_threshold=0.0,
        effect_operator=">=",
    )
    assert [e.id for e in fs.entities] == ["g1"]
    assert calls and calls[0]["significance"]["kind"] == "pvalue"
