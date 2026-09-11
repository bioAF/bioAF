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
