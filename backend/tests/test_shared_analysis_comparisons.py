"""plan_8_3 stage 7.1: one valid fit supplies every comparison it can validly make.

Execution and scoring centred on a single `level3.claim_index`, so one approved analysis produced one
claim comparison however many of the finding's claims its own output could answer. Study 50's F1 and
F4 rest on the same contrast at two different cutoffs (raw P < 0.01 with |log2FC| >= 1.5, and
adjusted P < 0.05 with |log2FC| >= 1); one appropriate statistical output supplies all four required
comparisons.

The templates export `gene_id`, `log2FoldChange`, `pvalue` and `padj` unfiltered, so each claim's own
predicate is applied to the stored statistics rather than to a list already thresholded for another
claim. Compatibility is checked first: a different contrast, a different design or a statistic the
export does not carry is a different analysis, and stays one.
"""

from app.services.validation_shared_analysis import (
    compatible_with,
    shared_claims,
)

_CONTRASTS = [
    {"name": "Mucoderm vs TCS", "reported_experiment_id": "e1"},
    {"name": "Mucoderm + HA vs Mucoderm", "reported_experiment_id": "e1"},
]


def _predicate(kind="padj", value=0.05, operator="<", effect=1.0, direction=None, count=None):
    return {
        "significance": {"kind": kind, "value": value, "operator": operator},
        "effect": {"kind": "abs_log2fc", "value": effect, "operator": ">="},
        "direction": direction,
        "count": count,
    }


def _target(index, contrast, predicate, **kw):
    return {
        "claim_index": index,
        "reported_experiment_id": "e1",
        "contrast_index": contrast,
        "predicate": predicate,
        "bound_key": None,
        **kw,
    }


# F1's two claims at raw P < 0.01; F4's two at adjusted P < 0.05. All four on contrast 0.
_TARGETS = [
    _target(0, 0, _predicate(kind="pvalue", value=0.01, effect=1.5, direction="up", count={"value": 1013})),
    _target(1, 0, _predicate(kind="pvalue", value=0.01, effect=1.5, direction="down", count={"value": 502})),
    _target(2, 0, _predicate(kind="padj", value=0.05, effect=1.0, direction="up")),
    _target(3, 0, _predicate(kind="padj", value=0.05, effect=1.0, direction="down")),
    _target(4, 1, _predicate(kind="padj", value=0.05, effect=1.0)),
]

_LEVEL3 = {
    "claim_index": 0,
    "contrast": "Mucoderm vs TCS",
    "source": "deposit",
    "method": "deseq2",
    "kind": "gene",
    "predicate": _TARGETS[0]["predicate"],
    "statistics": ["gene_id", "log2FoldChange", "pvalue", "padj"],
}


class TestWhichClaimsOneFitSupplies:
    def test_every_claim_on_the_same_contrast_and_experiment_is_supplied(self):
        supplied = shared_claims(_LEVEL3, targets=_TARGETS, contrasts=_CONTRASTS)
        assert [c["claim_index"] for c in supplied] == [0, 1, 2, 3]

    def test_a_claim_on_another_contrast_is_not(self):
        supplied = shared_claims(_LEVEL3, targets=_TARGETS, contrasts=_CONTRASTS)
        assert 4 not in [c["claim_index"] for c in supplied]

    def test_each_supplied_claim_carries_its_own_predicate_not_the_selected_one(self):
        supplied = {c["claim_index"]: c for c in shared_claims(_LEVEL3, targets=_TARGETS, contrasts=_CONTRASTS)}
        assert supplied[0]["predicate"]["significance"]["kind"] == "pvalue"
        assert supplied[2]["predicate"]["significance"]["kind"] == "padj"
        assert supplied[0]["predicate"]["effect"]["value"] == 1.5
        assert supplied[2]["predicate"]["effect"]["value"] == 1.0

    def test_a_claim_with_no_predicate_is_refused_with_its_reason(self):
        targets = [*_TARGETS[:2], _target(2, 0, None)]
        refused = [
            c
            for c in shared_claims(_LEVEL3, targets=targets, contrasts=_CONTRASTS, include_refused=True)
            if not c["supplied"]
        ]
        assert [c["claim_index"] for c in refused] == [2]
        assert "statistical definition" in refused[0]["reason"]

    def test_the_selected_claim_is_always_among_them(self):
        supplied = shared_claims(_LEVEL3, targets=_TARGETS, contrasts=_CONTRASTS)
        assert supplied[0]["claim_index"] == 0
        assert supplied[0]["selected"] is True


class TestCompatibility:
    def test_a_predicate_the_exported_statistics_can_answer_is_compatible(self):
        ok, reason = compatible_with(_LEVEL3, _predicate(kind="padj"))
        assert ok is True and reason is None

    def test_a_predicate_needing_a_statistic_the_export_does_not_carry_is_not(self):
        level3 = {**_LEVEL3, "statistics": ["gene_id", "log2FoldChange", "pvalue"]}
        ok, reason = compatible_with(level3, _predicate(kind="padj"))
        assert ok is False
        assert "adjusted p" in reason.lower()

    def test_an_export_whose_statistics_were_not_recorded_is_read_as_the_templates_contract(self):
        """Every current template exports gene_id, log2FoldChange, pvalue and padj unfiltered."""
        ok, reason = compatible_with({k: v for k, v in _LEVEL3.items() if k != "statistics"}, _predicate())
        assert ok is True

    def test_a_thresholded_export_cannot_answer_a_stricter_claim(self):
        level3 = {**_LEVEL3, "thresholded_at": {"significance": {"kind": "padj", "value": 0.05}}}
        ok, reason = compatible_with(level3, _predicate(kind="pvalue", value=0.01))
        assert ok is False
        assert "already filtered" in reason


class TestWhatCannotBeReused:
    def test_a_named_gene_claim_does_not_take_a_count_claims_comparison(self):
        targets = [*_TARGETS, _target(5, 0, _predicate(), kind="named_entities")]
        supplied = shared_claims(_LEVEL3, targets=targets, contrasts=_CONTRASTS, include_refused=True)
        named = next(c for c in supplied if c["claim_index"] == 5)
        assert named["supplied"] is True
        # It is supplied by the same fit, and its comparison is its own operation, never the count's.
        assert named["operation"] == "named_entities"
        assert supplied[0]["operation"] == "count"

    def test_a_different_statistical_method_is_a_different_analysis(self):
        limma = {**_LEVEL3, "method": "limma_trend"}
        targets = [_target(0, 0, _predicate(), method="deseq2")]
        refused = [
            c
            for c in shared_claims(limma, targets=targets, contrasts=_CONTRASTS, include_refused=True)
            if not c["supplied"]
        ]
        assert refused and "this analysis ran limma_trend" in refused[0]["reason"]

    def test_a_failed_binding_cannot_be_supplied(self):
        targets = [*_TARGETS[:1], _target(1, 0, _predicate(), binding_failed=True)]
        refused = [
            c
            for c in shared_claims(_LEVEL3, targets=targets, contrasts=_CONTRASTS, include_refused=True)
            if not c["supplied"]
        ]
        assert [c["claim_index"] for c in refused] == [1]
        assert "binding" in refused[0]["reason"]


class TestTheRecordedResults:
    def test_each_claim_gets_its_own_recorded_comparison_under_one_analysis_reference(self):
        from app.services.validation_shared_analysis import record_results

        results = record_results(
            _LEVEL3,
            {0: {"claim_count": {"count": 1013, "status": "agree"}}, 2: {"claim_count": {"count": 640}}},
            analysis_reference="execution-7",
        )
        assert set(results["per_claim"]) == {"0", "2"}
        assert results["analysis_reference"] == "execution-7"
        assert results["per_claim"]["0"]["claim_count"]["count"] == 1013

    def test_a_later_selection_does_not_overwrite_an_earlier_claims_comparison(self):
        from app.services.validation_shared_analysis import record_results

        first = record_results(_LEVEL3, {0: {"claim_count": {"count": 1013}}}, analysis_reference="execution-7")
        second = record_results(
            {**_LEVEL3, "claim_index": 2},
            {2: {"claim_count": {"count": 640}}},
            analysis_reference="execution-8",
            previous=first,
        )
        assert set(second["per_claim"]) == {"0", "2"}
        assert second["per_claim"]["0"]["analysis_reference"] == "execution-7"
        assert second["per_claim"]["2"]["analysis_reference"] == "execution-8"


class TestTheOutcomesReadThem:
    """plan_8_3 stage 7.1: a finding whose claims share a contrast is finished by one fit."""

    @staticmethod
    def _evidence(per_claim):
        return {
            "level3": {"claim_index": 0, "contrast": "Mucoderm vs TCS", "source": "deposit"},
            "level3_result": {"concordance": {"verdict": "agree", "recovered": 0.95, "paper_n": 100, "our_n": 98}},
            "level3_results": {"per_claim": per_claim, "analysis_reference": "execution-7"},
            "comparison_targets": [],
        }

    @staticmethod
    def _plan():
        return {
            "analysis_selection": {"current": {"revision": 1, "claim_index": 0}},
            "differential_design": {"contrasts": _CONTRASTS, "thresholds": {"padj": 0.05, "log2fc": 1.0}},
        }

    def _outcomes(self, per_claim, inventory):
        from app.services.validation_finding_outcomes import finding_outcomes

        return finding_outcomes(
            inventory,
            targets=[{"id": i, "contrast_index": t["contrast_index"]} for i, t in enumerate(_TARGETS)],
            plan=self._plan(),
            evidence=self._evidence(per_claim),
            study={"state": "classified"},
        )

    def test_a_finding_whose_two_claims_one_fit_supplied_is_assessed(self):
        agree = {
            "concordance": {"verdict": "agree", "recovered": 0.95, "paper_n": 100, "our_n": 98},
            "analysis_reference": "execution-7",
        }
        inventory = {
            "rubric_version": 1,
            "findings": [{"id": "F1", "required": [0, 1], "importance": {"category": "primary"}}],
        }
        outcomes = self._outcomes({"0": agree, "1": agree}, inventory)
        assert outcomes["F1"]["status"] == "supported"

    def test_without_the_second_claims_comparison_the_finding_is_not_assessed(self):
        agree = {
            "concordance": {"verdict": "agree", "recovered": 0.95, "paper_n": 100, "our_n": 98},
            "analysis_reference": "execution-7",
        }
        inventory = {
            "rubric_version": 1,
            "findings": [{"id": "F1", "required": [0, 1], "importance": {"category": "primary"}}],
        }
        outcomes = self._outcomes({"0": agree}, inventory)
        assert outcomes["F1"]["status"] != "supported"

    def test_a_claim_on_the_other_contrast_cannot_be_credited_from_this_fit(self):
        agree = {
            "concordance": {"verdict": "agree", "recovered": 0.95, "paper_n": 100, "our_n": 98},
            "analysis_reference": "execution-7",
        }
        inventory = {
            "rubric_version": 1,
            "findings": [{"id": "F6", "required": [0, 4], "importance": {"category": "primary"}}],
        }
        outcomes = self._outcomes({"0": agree}, inventory)
        assert outcomes["F6"]["status"] != "supported"
