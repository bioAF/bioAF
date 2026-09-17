"""plan_8_3 stage 2: choose an analysis by what it can FINISH, not by which claim reads best.

The selector ranked candidates by "has an authors' table, then needs no unresolved mapping, then the
paper's own order", one claim at a time. Study 50's F6 needs claims 13 and 14 on one contrast and
claim 15 on another; a run selected for claim 13 alone leaves F6 unassessed however well it goes. Two
other contrasts in the same paper each complete two whole findings from one statistical output.

Coverage is now computed before any outcome is known: which findings a candidate analysis could
complete, what that would be worth, and which prerequisites are still open. Ranking never looks at a
result, and a claim that names more genes does not outrank one that finishes a finding.
"""

import pytest

from app.services.validation_coverage import (
    analysis_candidates,
    coverage_of,
    rank_analyses,
)

_EXPERIMENTS = [{"id": "e1", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq"}]

# Four contrasts of one experiment, as study 50's paper reports them.
_CONTRASTS = [
    {"name": "Mucoderm vs TCS", "reported_experiment_id": "e1"},
    {"name": "collagen fleece vs TCS", "reported_experiment_id": "e1"},
    {"name": "Mucoderm + HA vs Mucoderm", "reported_experiment_id": "e1"},
    {"name": "collagen fleece + HA vs collagen fleece", "reported_experiment_id": "e1"},
]


def _target(index, contrast, **kw):
    return {
        "claim_index": index,
        "reported_experiment_id": "e1",
        "contrast_index": contrast,
        "claim_text": kw.pop("text", f"claim {index}"),
        **kw,
    }


# F1 and F4 both rest on contrast 0; F2 and F5 on contrast 1; F6 needs contrasts 2 AND 3.
_TARGETS = [
    _target(0, 0),
    _target(1, 0),
    _target(2, 0),
    _target(3, 0),
    _target(4, 1),
    _target(5, 1),
    _target(6, 1),
    _target(7, 1),
    _target(8, 2, text="1013 genes were up in Mucoderm + HA"),
    _target(9, 2),
    _target(10, 3),
]

_INVENTORY = {
    "rubric_version": 2,
    "findings": [
        {"id": "F1", "required": [0, 1], "importance": {"category": "primary"}},
        {"id": "F4", "required": [2, 3], "importance": {"category": "supporting"}},
        {"id": "F2", "required": [4, 5], "importance": {"category": "primary"}},
        {"id": "F5", "required": [6, 7], "importance": {"category": "supporting"}},
        {"id": "F6", "required": [8, 9, 10], "importance": {"category": "primary"}},
    ],
}


def _pairs(*claims, check="processed_reanalysis", status="available", requirement=None):
    return [
        {"claim_index": c, "check": check, "status": status, "requirement": requirement, "kind": "entity_set"}
        for c in claims
    ]


class TestACandidateIsAnAnalysis:
    def test_claims_of_one_contrast_and_experiment_are_one_candidate(self):
        candidates = analysis_candidates(_pairs(0, 1, 2, 3), targets=_TARGETS, contrasts=_CONTRASTS)
        assert len(candidates) == 1
        assert candidates[0]["contrast_index"] == 0
        assert sorted(candidates[0]["claim_indices"]) == [0, 1, 2, 3]

    def test_claims_of_different_contrasts_are_different_candidates(self):
        candidates = analysis_candidates(_pairs(0, 4, 8), targets=_TARGETS, contrasts=_CONTRASTS)
        assert sorted(c["contrast_index"] for c in candidates) == [0, 1, 2]

    def test_a_candidate_names_the_analysis_it_is_one_of(self):
        candidate = analysis_candidates(_pairs(0, 1), targets=_TARGETS, contrasts=_CONTRASTS)[0]
        assert candidate["analysis_key"]
        assert candidate["check"] == "processed_reanalysis"
        assert candidate["experiment_id"] == "e1"


class TestCoverageIsWhatACandidateCouldFinish:
    def test_one_analysis_that_supplies_every_required_claim_of_two_findings_completes_both(self):
        candidate = analysis_candidates(_pairs(0, 1, 2, 3), targets=_TARGETS, contrasts=_CONTRASTS)[0]
        cover = coverage_of(candidate, inventory=_INVENTORY)
        assert cover["completes"] == ["F1", "F4"]
        assert cover["partial"] == []
        assert cover["weight"] == 3

    def test_a_finding_spanning_two_contrasts_is_partial_and_names_what_is_still_needed(self):
        candidate = analysis_candidates(_pairs(8, 9), targets=_TARGETS, contrasts=_CONTRASTS)[0]
        cover = coverage_of(candidate, inventory=_INVENTORY)
        assert cover["completes"] == []
        assert cover["partial"] == ["F6"]
        assert cover["outstanding"] == {"F6": [10]}
        assert cover["weight"] == 0

    def test_a_claim_already_settled_counts_towards_completion(self):
        candidate = analysis_candidates(_pairs(8, 9), targets=_TARGETS, contrasts=_CONTRASTS)[0]
        cover = coverage_of(candidate, inventory=_INVENTORY, settled=[10])
        assert cover["completes"] == ["F6"]
        assert cover["outstanding"] == {}

    def test_a_technical_finding_is_worth_nothing_and_still_counts_as_completed(self):
        inventory = {
            "rubric_version": 2,
            "findings": [{"id": "T1", "required": [0], "importance": {"category": "technical"}}],
        }
        candidate = analysis_candidates(_pairs(0), targets=_TARGETS, contrasts=_CONTRASTS)[0]
        cover = coverage_of(candidate, inventory=inventory)
        assert cover["completes"] == ["T1"]
        assert cover["weight"] == 0


class TestFeasibilityBeforeRanking:
    def test_a_candidate_whose_mapping_is_unresolved_is_not_ready_and_says_why(self):
        candidate = analysis_candidates(
            _pairs(8, 9, status="unresolved", requirement="sample_mapping"),
            targets=_TARGETS,
            contrasts=_CONTRASTS,
        )[0]
        assert candidate["ready"] is False
        assert candidate["prerequisites"] == ["sample_mapping"]

    def test_a_candidate_with_every_claim_available_is_ready(self):
        candidate = analysis_candidates(_pairs(0, 1), targets=_TARGETS, contrasts=_CONTRASTS)[0]
        assert candidate["ready"] is True
        assert candidate["prerequisites"] == []

    def test_a_not_ready_candidate_is_ranked_below_every_ready_one_however_much_it_would_cover(self):
        pairs = _pairs(0, 1) + _pairs(4, 5, 6, 7, status="unresolved", requirement="sample_mapping")
        ranked = rank_analyses(analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS), inventory=_INVENTORY)
        assert ranked[0]["contrast_index"] == 0
        assert ranked[0]["ready"] is True
        assert ranked[-1]["ready"] is False


class TestRankingIsByWhatWouldBeFinished:
    def test_a_contrast_that_completes_two_findings_beats_one_that_completes_none(self):
        """Study 50: Mucoderm versus TCS finishes F1 and F4; the HA contrast finishes nothing on its own."""
        pairs = _pairs(0, 1, 2, 3) + _pairs(8, 9)
        ranked = rank_analyses(analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS), inventory=_INVENTORY)
        assert ranked[0]["contrast_index"] == 0
        assert ranked[0]["coverage"]["completes"] == ["F1", "F4"]

    def test_weight_decides_before_the_number_of_findings(self):
        """A primary finding is worth 2 and a supporting one 1, so one primary beats one supporting."""
        inventory = {
            "rubric_version": 2,
            "findings": [
                {"id": "P", "required": [0], "importance": {"category": "primary"}},
                {"id": "S1", "required": [4], "importance": {"category": "supporting"}},
            ],
        }
        ranked = rank_analyses(
            analysis_candidates(_pairs(0, 4), targets=_TARGETS, contrasts=_CONTRASTS), inventory=inventory
        )
        assert ranked[0]["coverage"]["completes"] == ["P"]

    def test_at_equal_weight_the_candidate_that_finishes_more_findings_wins(self):
        inventory = {
            "rubric_version": 2,
            "findings": [
                {"id": "P", "required": [0], "importance": {"category": "primary"}},
                {"id": "S1", "required": [4], "importance": {"category": "supporting"}},
                {"id": "S2", "required": [5], "importance": {"category": "supporting"}},
            ],
        }
        ranked = rank_analyses(
            analysis_candidates(_pairs(0, 4, 5), targets=_TARGETS, contrasts=_CONTRASTS), inventory=inventory
        )
        assert ranked[0]["coverage"]["completes"] == ["S1", "S2"]
        assert ranked[0]["coverage"]["weight"] == 2

    def test_two_candidates_that_cover_the_same_are_separated_by_a_stable_tie_breaker(self):
        pairs = _pairs(0, 1, 2, 3) + _pairs(4, 5, 6, 7)
        candidates = analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS)
        first = rank_analyses(candidates, inventory=_INVENTORY)
        second = rank_analyses(list(reversed(candidates)), inventory=_INVENTORY)
        assert [c["analysis_key"] for c in first] == [c["analysis_key"] for c in second]
        assert first[0]["contrast_index"] == 0

    def test_an_eligible_author_result_check_is_preferred_over_new_compute(self):
        pairs = _pairs(0, 1, 2, 3) + _pairs(4, 5, 6, 7, check="author_results")
        ranked = rank_analyses(analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS), inventory=_INVENTORY)
        assert ranked[0]["check"] == "author_results"

    def test_the_ranking_policy_and_its_version_are_recorded_on_every_candidate(self):
        ranked = rank_analyses(
            analysis_candidates(_pairs(0, 1), targets=_TARGETS, contrasts=_CONTRASTS), inventory=_INVENTORY
        )
        assert ranked[0]["ranking"]["policy"]
        assert ranked[0]["ranking"]["version"] >= 1
        assert ranked[0]["ranking"]["rank"] == 1


class TestRankingNeverLooksAtAnOutcome:
    def test_an_agreeing_result_does_not_move_a_candidate(self):
        pairs = _pairs(0, 1, 2, 3) + _pairs(8, 9)
        plain = rank_analyses(analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS), inventory=_INVENTORY)
        with_outcomes = rank_analyses(
            analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS),
            inventory=_INVENTORY,
            # Whatever an earlier run concluded, it is not an input to the ranking.
            settled=[],
        )
        assert [c["analysis_key"] for c in plain] == [c["analysis_key"] for c in with_outcomes]

    def test_a_claim_naming_many_genes_does_not_outrank_one_that_finishes_a_finding(self):
        pairs = _pairs(0, 1, 2, 3) + _pairs(8, 9)
        ranked = rank_analyses(analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS), inventory=_INVENTORY)
        assert "1013 genes" not in (_TARGETS[ranked[0]["claim_indices"][0]]["claim_text"])


class TestADeclaredChoiceIsNotOverridden:
    def test_an_explicit_claim_choice_keeps_its_candidate_at_the_top(self):
        pairs = _pairs(0, 1, 2, 3) + _pairs(8, 9)
        ranked = rank_analyses(
            analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS),
            inventory=_INVENTORY,
            chosen_claim=8,
        )
        assert ranked[0]["claim_indices"] == [8, 9]
        assert ranked[0]["ranking"]["policy"] == "explicit_choice"

    @pytest.mark.parametrize("claim", [99, None])
    def test_a_choice_that_names_no_candidate_leaves_the_ranking_alone(self, claim):
        pairs = _pairs(0, 1, 2, 3) + _pairs(8, 9)
        ranked = rank_analyses(
            analysis_candidates(pairs, targets=_TARGETS, contrasts=_CONTRASTS),
            inventory=_INVENTORY,
            chosen_claim=claim,
        )
        assert ranked[0]["contrast_index"] == 0


class TestTheSelectorUsesIt:
    """The selection record carries the coverage that chose it, and the choice follows it."""

    @staticmethod
    async def _select(targets, checks, *, inventory, autonomous=False, chosen=None):
        from app.services.validation_selection import select_analysis

        return await select_analysis(
            targets,
            checks,
            experiments=_EXPERIMENTS,
            contrasts=_CONTRASTS,
            route="deposit",
            autonomous=autonomous,
            client=None,
            model=None,
            api_key=None,
            inventory=inventory,
            chosen_claim=chosen,
        )

    @staticmethod
    def _checks(available, *, unresolved=()):
        rows = []
        for index in range(len(_TARGETS)):
            if index in available:
                rows.append({"processed_reanalysis": {"status": "available"}})
            elif index in unresolved:
                rows.append({"processed_reanalysis": {"status": "unresolved", "requirement": "sample_mapping"}})
            else:
                rows.append({"processed_reanalysis": {"status": "unavailable", "reason": "no input"}})
        return rows

    @pytest.mark.asyncio
    async def test_it_prefers_the_contrast_that_completes_two_findings_over_the_isolated_one(self):
        record = await self._select(_TARGETS, self._checks({0, 1, 2, 3, 8, 9}), inventory=_INVENTORY)
        assert record["current"]["contrast_index"] == 0
        assert record["current"]["coverage"]["completes"] == ["F1", "F4"]

    @pytest.mark.asyncio
    async def test_it_records_every_candidate_analysis_with_its_coverage_and_ranking(self):
        record = await self._select(_TARGETS, self._checks({0, 1, 2, 3, 8, 9}), inventory=_INVENTORY)
        analyses = record["analyses"]
        assert len(analyses) == 2
        assert all("coverage" in a and "ranking" in a for a in analyses)
        assert analyses[0]["ranking"]["rank"] == 1

    @pytest.mark.asyncio
    async def test_a_finding_spanning_two_contrasts_shows_what_is_still_needed(self):
        record = await self._select(_TARGETS, self._checks({8, 9}), inventory=_INVENTORY)
        assert record["current"]["coverage"]["partial"] == ["F6"]
        assert record["current"]["coverage"]["outstanding"] == {"F6": [10]}

    @pytest.mark.asyncio
    async def test_an_unresolved_candidate_is_not_described_as_runnable(self):
        record = await self._select(_TARGETS, self._checks({8, 9}, unresolved={0, 1, 2, 3}), inventory=_INVENTORY)
        not_ready = [a for a in record["analyses"] if not a["ready"]]
        assert [a["prerequisites"] for a in not_ready] == [["sample_mapping"]]
        assert record["current"]["contrast_index"] == 2

    @pytest.mark.asyncio
    async def test_without_an_inventory_it_selects_as_it_did_before(self):
        record = await self._select(_TARGETS, self._checks({0, 8}), inventory=None)
        assert record["current"] is not None
        assert record["current"]["coverage"] is None


class TestReselectingOnceTheFindingsAreEstablished:
    """The selection runs before the inventory stage, so coverage is applied when the findings land."""

    @staticmethod
    def _record(claim_index, contrast_index, candidates):
        return {
            "current": {
                "revision": 1,
                "claim_index": claim_index,
                "contrast_index": contrast_index,
                "check": "processed_reanalysis",
                "reported_experiment_id": "e1",
                "workflow": "nf-core/rnaseq",
                "decided_by": "model",
                "coverage": None,
            },
            "history": [],
            "candidates": candidates,
            "unassessed": [],
        }

    def test_it_moves_to_the_analysis_that_completes_two_findings(self):
        from app.services.validation_selection import reselect_for_coverage

        record = self._record(8, 2, _pairs(0, 1, 2, 3) + _pairs(8, 9))
        revised, changed = reselect_for_coverage(record, targets=_TARGETS, contrasts=_CONTRASTS, inventory=_INVENTORY)
        assert changed is True
        assert revised["current"]["contrast_index"] == 0
        assert revised["current"]["coverage"]["completes"] == ["F1", "F4"]
        assert revised["history"][-1]["claim_index"] == 8
        assert any(u["claim_index"] == 8 for u in revised["unassessed"])

    def test_it_leaves_a_selection_that_is_already_the_best_alone(self):
        from app.services.validation_selection import reselect_for_coverage

        record = self._record(0, 0, _pairs(0, 1, 2, 3) + _pairs(8, 9))
        revised, changed = reselect_for_coverage(record, targets=_TARGETS, contrasts=_CONTRASTS, inventory=_INVENTORY)
        assert changed is False
        assert revised["current"]["claim_index"] == 0
        assert revised["current"]["coverage"]["completes"] == ["F1", "F4"]

    def test_it_never_replaces_a_choice_a_person_made(self):
        from app.services.validation_selection import reselect_for_coverage

        record = self._record(8, 2, _pairs(0, 1, 2, 3) + _pairs(8, 9))
        record["current"]["decided_by"] = "human"
        revised, changed = reselect_for_coverage(record, targets=_TARGETS, contrasts=_CONTRASTS, inventory=_INVENTORY)
        assert changed is False
        assert revised["current"]["claim_index"] == 8
        # It still says what that choice covers, and what it leaves outstanding.
        assert revised["current"]["coverage"]["partial"] == ["F6"]

    def test_an_inventory_that_is_still_pending_changes_nothing(self):
        from app.services.validation_selection import reselect_for_coverage

        record = self._record(8, 2, _pairs(0, 1, 2, 3) + _pairs(8, 9))
        revised, changed = reselect_for_coverage(
            record, targets=_TARGETS, contrasts=_CONTRASTS, inventory={"status": "pending"}
        )
        assert changed is False
        assert revised["current"]["claim_index"] == 8

    def test_a_record_with_no_selection_is_left_as_it_is(self):
        from app.services.validation_selection import reselect_for_coverage

        revised, changed = reselect_for_coverage(
            {"current": None, "candidates": []}, targets=_TARGETS, contrasts=_CONTRASTS, inventory=_INVENTORY
        )
        assert changed is False
        assert revised["current"] is None
