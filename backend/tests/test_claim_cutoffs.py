"""change_7.3 section 7: cutoffs belong to claims.

A ComparisonTarget had one scalar `threshold` and no link to a contrast, so "padj < 0.05 and |log2FC|
> 2" could not be stored. The extractor gave each contrast one threshold pair and that pair drove the
ground truth, so Groff's "XX vs XY" ground truth would have been the 88-gene subset while the paper's
headline claim for it is the 194. Where two claims on one contrast use different cutoffs, they are two
claims and the contrast never merges them.

Every value here is deliberately unlike Groff's.
"""

import pytest

from app.services.validation_claim_cutoffs import claim_cutoffs, contrast_index_for, derive_contrast_thresholds

_CONTRASTS = [
    {"name": "knockout vs wild type liver", "thresholds": {"padj": 0.01, "log2fc": 1.5}},
    {"name": "treated vs vehicle kidney", "thresholds": {"padj": 0.05, "log2fc": None}},
]

_BROAD = {
    "claim_text": "312 genes were differentially expressed (FDR < 0.01)",
    "value": 312,
    "output_type": "gene_set_size",
    "contrast": "knockout vs wild type liver",
    "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.01}],
}
_SUBSET = {
    "claim_text": "of which 41 changed more than 1.5-fold in log2",
    "value": 41,
    "output_type": "gene_set_size",
    "contrast": "knockout vs wild type liver",
    "cutoffs": [
        {"kind": "padj", "operator": "<", "value": 0.01},
        {"kind": "abs_log2fc", "operator": ">", "value": 1.5},
    ],
}


class TestACutoffSetIsStructure:
    def test_a_two_part_cutoff_is_kept_whole(self):
        assert claim_cutoffs(_SUBSET) == [
            {"kind": "padj", "operator": "<", "value": 0.01},
            {"kind": "abs_log2fc", "operator": ">", "value": 1.5},
        ]

    def test_a_scalar_threshold_becomes_one_cutoff(self):
        assert claim_cutoffs({"threshold": 0.05, "threshold_kind": "padj"}) == [
            {"kind": "padj", "operator": "<", "value": 0.05}
        ]

    def test_an_fdr_is_an_adjusted_p(self):
        assert claim_cutoffs({"cutoffs": [{"kind": "FDR", "operator": "<=", "value": 0.1}]})[0]["kind"] == "padj"

    @pytest.mark.parametrize(
        "bad",
        [
            {"kind": "vibes", "operator": "<", "value": 1},
            {"kind": "padj", "operator": "~", "value": 1},
            {"kind": "padj"},
        ],
    )
    def test_an_unusable_cutoff_is_dropped_not_invented(self, bad):
        assert claim_cutoffs({"cutoffs": [bad]}) == []


class TestAClaimKnowsItsContrast:
    def test_by_name(self):
        assert contrast_index_for(_BROAD, _CONTRASTS) == 0

    def test_by_index(self):
        assert contrast_index_for({"contrast": 1}, _CONTRASTS) == 1

    def test_an_unknown_contrast_is_none(self):
        assert contrast_index_for({"contrast": "something else"}, _CONTRASTS) is None


class TestTwoClaimsOnOneContrastStayTwo:
    def test_each_keeps_its_own_cutoffs(self):
        assert claim_cutoffs(_BROAD) != claim_cutoffs(_SUBSET)

    def test_the_contrast_takes_the_finding_claim_s_cutoff_not_the_subset_s(self):
        contrasts = derive_contrast_thresholds([dict(c) for c in _CONTRASTS], [_SUBSET, _BROAD])
        assert contrasts[0]["thresholds"] == {"padj": 0.01, "log2fc": None}

    def test_a_named_finding_claim_wins(self):
        contrasts = [dict(c) for c in _CONTRASTS]
        contrasts[0]["finding_claim_index"] = 0
        derived = derive_contrast_thresholds(contrasts, [_SUBSET, _BROAD])
        assert derived[0]["thresholds"] == {"padj": 0.01, "log2fc": 1.5}

    def test_it_records_which_claim_the_threshold_came_from(self):
        contrasts = derive_contrast_thresholds([dict(c) for c in _CONTRASTS], [_SUBSET, _BROAD])
        assert contrasts[0]["thresholds_from_claim"] == 1

    def test_a_contrast_with_no_linked_claim_keeps_its_own_pair(self):
        contrasts = derive_contrast_thresholds([dict(c) for c in _CONTRASTS], [_SUBSET, _BROAD])
        assert contrasts[1]["thresholds"] == {"padj": 0.05, "log2fc": None}


class TestTheCutoffsArePersisted:
    @pytest.mark.asyncio
    async def test_a_target_stores_its_contrast_and_cutoffs(self, session, admin_user):
        from app.services.reproduction_plan_service import ReproductionPlanService
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        plan = await ReproductionPlanService.create_plan(session, study, admin_user.id)
        [target] = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {
                    "metric_key": "",
                    "claim_text": _SUBSET["claim_text"],
                    "claimed_value": 41,
                    "contrast_index": 0,
                    "cutoffs": claim_cutoffs(_SUBSET),
                }
            ],
        )
        assert target.contrast_index == 0
        assert len(target.cutoffs) == 2


class TestTheExtractionCarriesThem:
    def test_the_schema_asks_for_a_contrast_and_cutoffs_per_claim(self):
        from app.services.validation_extraction_service import build_extraction_prompt

        system, _ = build_extraction_prompt("text")
        assert '"cutoffs"' in system
        assert '"contrast"' in system
        assert '"finding_claim_index"' in system

    def test_the_prompt_says_a_set_and_its_subset_are_two_claims(self):
        from app.services.validation_extraction_service import build_extraction_prompt

        system, _ = build_extraction_prompt("text")
        assert "subset" in system.lower()

    def test_a_typed_blocker_is_parsed_to_its_text_and_kind(self):
        from app.services.validation_extraction_service import parse_extraction

        parsed = parse_extraction(
            '```json\n{"blockers": [{"text": "Sample IDs are not enumerated", "kind": "sample_assignment"}, '
            '"a plain string blocker"]}\n```'
        )
        assert parsed["blockers"] == ["Sample IDs are not enumerated", "a plain string blocker"]
        assert parsed["blocker_kinds"] == [{"text": "Sample IDs are not enumerated", "kind": "sample_assignment"}]


class TestTheResultsTableIsMeasuredAtEveryClaimedFoldChange:
    @pytest.mark.asyncio
    async def test_a_fold_change_stated_only_as_a_cutoff_is_measured(self, session, admin_user):
        from app.services.reproduction_plan_service import ReproductionPlanService
        from app.services.validation_assessment import claimed_thresholds
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        plan = await ReproductionPlanService.create_plan(session, study, admin_user.id)
        study.reproduction_plan_id = plan.id
        await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {
                    "metric_key": "",
                    "claim_text": _SUBSET["claim_text"],
                    "claimed_value": 41,
                    "cutoffs": claim_cutoffs(_SUBSET),
                }
            ],
        )
        assert await claimed_thresholds(session, study) == [1.5]


# ---- change_7.4 section 1.6: one statistical definition, carried whole, never defaulted ----

_P_ONLY = {
    "claim_text": "We identified 87 significantly (P < 0.005) down-regulated genes",
    "value": 87,
    "output_type": "gene_set_size",
    "contrast": "knockout vs wild type liver",
    "threshold": 0.005,
    "threshold_kind": "pvalue",
    "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.005}],
}
_FOLD = {
    "claim_text": "63 genes changed more than threefold (FDR <= 0.1)",
    "value": 63,
    "output_type": "gene_set_size",
    "contrast": "treated vs vehicle kidney",
    "cutoffs": [
        {"kind": "padj", "operator": "<=", "value": 0.1},
        {"kind": "fold_change", "operator": ">", "value": 3},
    ],
}


class TestEveryCutoffKindSurvivesDerivation:
    """Study 37's claim said P < 0.01 and the contrast came out `{"padj": None, "log2fc": None}`:
    `derive_contrast_thresholds` kept only two kinds and dropped the rest."""

    def test_a_p_value_cutoff_and_its_operator_reach_the_contrast(self):
        [contrast, _] = derive_contrast_thresholds([dict(c) for c in _CONTRASTS], [_P_ONLY])
        assert contrast["cutoffs"] == [{"kind": "pvalue", "operator": "<", "value": 0.005}]

    def test_a_fold_change_cutoff_and_its_operator_reach_the_contrast(self):
        [_, contrast] = derive_contrast_thresholds([dict(c) for c in _CONTRASTS], [_FOLD])
        assert {"kind": "fold_change", "operator": ">", "value": 3.0} in contrast["cutoffs"]
        assert {"kind": "padj", "operator": "<=", "value": 0.1} in contrast["cutoffs"]


class TestAThresholdThatDisagreesWithItsCutoffsIsUnresolved:
    """Study 37's binding said `padj 0.01` and the same claim's cutoffs said `P < 0.01`. Neither may
    overwrite the other."""

    def test_the_disagreement_is_named(self):
        from app.services.validation_claim_cutoffs import threshold_disagreement

        reason = threshold_disagreement(0.01, "padj", [{"kind": "pvalue", "operator": "<", "value": 0.01}])
        assert reason
        assert "adjusted" in reason and "P" in reason

    def test_an_agreeing_threshold_is_not_a_disagreement(self):
        from app.services.validation_claim_cutoffs import threshold_disagreement

        assert threshold_disagreement(0.01, "pvalue", [{"kind": "pvalue", "operator": "<", "value": 0.01}]) is None
        assert (
            threshold_disagreement(
                1.0,
                "abs_log2fc",
                [
                    {"kind": "pvalue", "operator": "<", "value": 0.01},
                    {"kind": "abs_log2fc", "operator": ">", "value": 1},
                ],
            )
            is None
        )

    def test_the_contrast_it_defines_is_unresolved(self):
        claim = {**_P_ONLY, "threshold": 0.005, "threshold_kind": "padj"}
        [contrast, _] = derive_contrast_thresholds([dict(c) for c in _CONTRASTS], [claim])
        assert contrast["thresholds_unresolved"]


class TestTheAnalysisCutoffsAreNeverDefaulted:
    def _cutoffs(self, contrast, design=None):
        from app.services.validation_claim_cutoffs import analysis_cutoffs

        return analysis_cutoffs(contrast, design or {})

    def test_a_claim_s_padj_cutoff_with_no_effect_cutoff_states_no_effect_requirement(self):
        out = self._cutoffs({"cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}]})
        assert out["refusal"] is None
        assert out["padj_threshold"] == 0.05
        assert out["lfc_threshold"] == 0.0
        assert "no fold-change requirement" in out["statement"]

    def test_a_fold_change_is_evaluated_on_the_log2_scale(self):
        out = self._cutoffs({"cutoffs": _FOLD["cutoffs"]})
        assert out["refusal"] is None
        assert out["lfc_threshold"] == pytest.approx(1.5849625, rel=1e-6)

    def test_a_raw_p_value_is_refused_until_the_route_can_apply_it(self):
        out = self._cutoffs({"cutoffs": _P_ONLY["cutoffs"]})
        assert out["padj_threshold"] is None
        assert "P value" in out["refusal"]

    def test_a_missing_significance_cutoff_is_refused(self):
        out = self._cutoffs(
            {"thresholds": {"padj": None, "log2fc": 1.0}}, {"thresholds": {"padj": None, "log2fc": 1.0}}
        )
        assert out["refusal"]
        assert out["padj_threshold"] is None

    def test_an_unresolved_threshold_is_refused(self):
        out = self._cutoffs({"cutoffs": _P_ONLY["cutoffs"], "thresholds_unresolved": "they disagree"})
        assert "they disagree" in out["refusal"]

    def test_an_unstated_effect_requirement_is_refused_rather_than_filled(self):
        """Only the paper-level pair, with its fold change left null: nothing says there is no
        requirement, so bioAF does not supply one."""
        out = self._cutoffs({}, {"thresholds": {"padj": 0.05, "log2fc": None}})
        assert out["refusal"]
        assert "fold" in out["refusal"]

    def test_a_contrast_s_own_null_fold_change_means_it_does_not_apply(self):
        """The extraction contract: a contrast sets a cutoff to null where it does not apply."""
        out = self._cutoffs({"thresholds": {"padj": 0.05, "log2fc": None}})
        assert out["refusal"] is None
        assert out["lfc_threshold"] == 0.0


class TestTheVocabulariesAgree:
    def test_the_binding_prompt_offers_a_p_value(self):
        from app.services.validation_extraction_service import build_binding_prompt

        system, _ = build_binding_prompt([{"metric_key": "x", "claim_text": "y"}])
        assert '"threshold_kind": "padj | pvalue | abs_log2fc | null"' in system

    def test_the_extraction_schema_offers_a_p_value_for_a_claim_s_threshold(self):
        from app.services.validation_extraction_service import build_extraction_prompt

        system, _ = build_extraction_prompt("text")
        assert '"threshold_kind": "padj | pvalue | abs_log2fc | null"' in system


_DISAGREEING_PAPER = """```json
{"accessions": ["GSE1"],
 "method": {"assay": "bulk RNA-seq", "tools": ["DESeq2"], "reference_build": "GRCm38"},
 "differential_design": {"contrasts": [
   {"name": "knockout vs wild type liver", "assay": "bulk RNA-seq", "finding_claim_index": 0,
    "thresholds": {"log2fc": null, "padj": null}}],
  "thresholds": {"log2fc": null, "padj": null}},
 "claims": [{"metric_key": "", "claim_text": "We identified 87 significantly (P < 0.005) down-regulated genes",
   "value": 87, "unit": "genes", "output_type": "gene_set_size", "contrast": "knockout vs wild type liver",
   "threshold": 0.005, "threshold_kind": "padj",
   "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.005}]}],
 "data_availability": "deposited", "blockers": []}
```"""


class TestADisagreementIsRecordedNotResolvedBySilence:
    @pytest.mark.asyncio
    async def test_the_extraction_marks_the_claim_unresolved_and_records_an_issue(
        self, session, admin_user, monkeypatch
    ):
        from app.services import validation_extraction_service as ext
        from app.services.validation_issue_service import ValidationIssueService
        from app.services.validation_study_service import ValidationStudyService
        from tests.test_validation_extraction import _patch_llm

        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        await session.flush()
        _patch_llm(monkeypatch, _DISAGREEING_PAPER)

        from app.services.reproduction_plan_service import ReproductionPlanService

        await ext.ValidationExtractionService.extract(session, study, "TEXT", admin_user.organization_id, admin_user.id)
        await session.flush()
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)

        [target] = plan.comparison_targets
        assert target.unresolved_reason and "disagrees" in target.unresolved_reason
        # Neither reading overwrote the other.
        assert target.threshold_kind == "padj"
        assert target.cutoffs == [{"kind": "pvalue", "operator": "<", "value": 0.005}]
        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert any("disagrees" in (i.get("message") or "") for i in issues)
        assert plan.differential_design_json["contrasts"][0]["thresholds_unresolved"]
