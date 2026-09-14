"""plan_8_2 section 3.1: one canonical predicate end to end, with the paper's own expression kept.

A stated twofold threshold is an absolute log2 fold change of one, with its strict or inclusive operator
kept. Equivalent representations are normalized before they are called contradictory, and a redundant
extracted scalar never silently replaces what the stated cutoffs mean.
"""

from app.services.validation_claim_cutoffs import threshold_disagreement
from app.services.validation_predicate import build_predicate, predicate_words

_PADJ = {"kind": "padj", "operator": "<", "value": 0.05}


def _claim(**overrides):
    return {
        "claim_text": "1,904 genes were more than twofold misregulated",
        "claimed_value": 1904,
        "output_type": "gene_set_size",
        "contrast_index": 0,
        **overrides,
    }


class TestEquivalentRepresentations:
    def test_a_twofold_threshold_and_an_absolute_log2_threshold_of_one_agree(self):
        stated = [_PADJ, {"kind": "fold_change", "operator": ">", "value": 2}]
        assert threshold_disagreement(1.0, "abs_log2fc", stated) is None
        assert (
            threshold_disagreement(2.0, "fold_change", [{"kind": "abs_log2fc", "operator": ">", "value": 1.0}]) is None
        )

    def test_different_effect_sizes_still_disagree(self):
        reason = threshold_disagreement(1.5, "abs_log2fc", [{"kind": "fold_change", "operator": ">", "value": 2}])
        assert reason and "disagrees" in reason

    def test_a_p_value_and_an_adjusted_p_value_are_never_equivalent(self):
        assert threshold_disagreement(0.01, "padj", [{"kind": "pvalue", "operator": "<", "value": 0.01}])


class TestThePredicateKeepsThePapersExpression:
    def test_a_stated_twofold_cutoff_is_applied_as_log2_of_one_with_its_operator_and_its_words_kept(self):
        predicate = build_predicate(_claim(cutoffs=[_PADJ, {"kind": "fold_change", "operator": ">=", "value": 2}]))
        assert predicate["effect"]["kind"] == "abs_log2fc"
        assert (predicate["effect"]["operator"], predicate["effect"]["value"]) == (">=", 1.0)
        assert predicate["effect"]["stated"] == {"kind": "fold_change", "operator": ">=", "value": 2.0}
        assert "fold change >= 2" in predicate_words(predicate)

    def test_a_strict_twofold_cutoff_stays_strict(self):
        predicate = build_predicate(_claim(cutoffs=[_PADJ, {"kind": "fold_change", "operator": ">", "value": 2}]))
        assert predicate["effect"]["operator"] == ">"

    def test_a_redundant_scalar_never_replaces_the_stated_cutoffs(self):
        predicate = build_predicate(
            _claim(
                cutoffs=[_PADJ, {"kind": "fold_change", "operator": ">", "value": 2}],
                threshold=1.0,
                threshold_kind="abs_log2fc",
            )
        )
        assert predicate["status"] == "resolved"
        assert predicate["effect"]["stated"]["kind"] == "fold_change"
        assert predicate["significance"]["kind"] == "padj"

    def test_the_predicate_says_where_its_cutoffs_came_from(self):
        predicate = build_predicate(_claim(cutoffs=[_PADJ]))
        assert predicate["cutoff_source"] == {"kind": "claim"}


def test_keeping_the_papers_expression_never_re_evaluates_a_check():
    """Decision 5: historical checks are not rerun because bioAF now presents a cutoff better."""
    from app.services.validation_check_queue import fingerprint
    from app.services.validation_predicate import predicate_identity

    predicate = build_predicate(_claim(cutoffs=[_PADJ, {"kind": "fold_change", "operator": ">", "value": 2}]))
    as_built_before = {k: v for k, v in predicate.items() if k != "cutoff_source"}
    as_built_before["effect"] = {k: v for k, v in predicate["effect"].items() if k != "stated"}
    assert fingerprint(predicate_identity(predicate)) == fingerprint(as_built_before)
