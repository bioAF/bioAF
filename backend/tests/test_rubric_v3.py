"""plan_8_4: rubric version 3, the evidence-based Validation Scorecard.

Version 2 divided agreement by the weight of assessed scientific FINDINGS, so a paper whose data bioAF
cannot acquire could never earn anything, however much of its code, metadata and methods bioAF had
actually checked. Version 3 scores what was established: 100 points allocated across code, sample
metadata, experimental methods, computational methods and results, each leaf obligation verified,
failed or undetermined, and the score is the sum of the verified weights. Nothing is divided by what
was assessed, nothing is subtracted, and no model produces the number.

These tests are the arithmetic contract (section 9). They are pure: no database, no model.
"""

from fractions import Fraction

import pytest

from app.services.validation_rubric_v3 import (
    RUBRIC_VERSION,
    SECTION_MAXIMA,
    UNDETERMINED,
    VERIFIED,
    FAILED,
    ApplicabilityUncertain,
    allocate,
    default_profile,
    score,
)


class TestTheRubricIsDeclaredOnceAndAddsToOneHundred:
    def test_the_five_sections_carry_their_stated_maxima(self):
        assert SECTION_MAXIMA == {"C": 20, "S": 15, "E": 15, "M": 20, "R": 30}
        assert sum(SECTION_MAXIMA.values()) == 100
        assert RUBRIC_VERSION == 3

    def test_every_documentary_criterion_splits_into_two_equal_obligations(self):
        """Section 3.2: partial credit is explicit. A four-point row is 2 and 2, so "2 verified, 2
        failed" is a statement about two named obligations and not a half-good rating."""
        leaves = allocate(default_profile())
        for section in ("C", "S", "E", "M"):
            rows: dict[str, list] = {}
            for leaf in leaves:
                if leaf["section"] == section:
                    rows.setdefault(leaf["criterion"], []).append(leaf)
            for criterion, pair in rows.items():
                assert sorted(l["obligation"] for l in pair) == ["A", "B"], criterion
                assert pair[0]["weight"] == pair[1]["weight"], criterion

    def test_the_allocated_weights_sum_to_one_hundred_exactly(self):
        leaves = allocate(default_profile())
        assert sum((leaf["weight"] for leaf in leaves), Fraction(0)) == 100

    def test_each_section_allocates_exactly_its_maximum(self):
        leaves = allocate(default_profile())
        for section, maximum in SECTION_MAXIMA.items():
            total = sum((leaf["weight"] for leaf in leaves if leaf["section"] == section), Fraction(0))
            assert total == maximum, section

    def test_the_documentary_sections_can_reach_seventy_and_no_more(self):
        """Section 3.1: the first four sections are 70 points, and author-results consistency adds at
        most 8. A paper assessed only through its documentation and its authors' own results cannot
        exceed 78, whatever else is true of it."""
        leaves = allocate(default_profile())
        documentary = sum((l["weight"] for l in leaves if l["section"] != "R"), Fraction(0))
        author_results = sum((l["weight"] for l in leaves if l["criterion"] == "R1"), Fraction(0))
        assert documentary == 70
        assert author_results == 8
        assert documentary + author_results == 78


class TestTheScoreIsTheSumOfWhatWasVerified:
    def _leaves(self):
        return allocate(default_profile())

    def test_nothing_assessed_is_zero_verified_and_one_hundred_undetermined(self):
        card = score(self._leaves(), {})
        assert card["verified"] == 0
        assert card["failed"] == 0
        assert card["undetermined"] == 100
        assert card["overall_score"] == 0
        assert card["assessed_points"] == 0
        assert card["status"] == "not_assessed"

    def test_verified_failed_and_undetermined_always_add_to_one_hundred(self):
        leaves = self._leaves()
        outcomes = {}
        for position, leaf in enumerate(leaves):
            outcomes[leaf["id"]] = {"outcome": (VERIFIED, FAILED, UNDETERMINED)[position % 3]}
        card = score(leaves, outcomes)
        assert card["verified"] + card["failed"] + card["undetermined"] == 100

    def test_the_score_is_the_verified_sum_with_nothing_divided_and_nothing_subtracted(self):
        leaves = self._leaves()
        outcomes = {leaf["id"]: {"outcome": FAILED} for leaf in leaves}
        for leaf in leaves:
            if leaf["criterion"] in ("C1", "C2"):
                outcomes[leaf["id"]] = {"outcome": VERIFIED}
        card = score(leaves, outcomes)
        assert card["verified"] == 8
        assert card["overall_score"] == 8
        assert card["failed"] == 92
        assert card["assessed_points"] == 100

    def test_the_owners_code_example_is_eighteen_of_twenty_with_two_failed(self):
        """Section 4: the first four code rows fully verified and one of C5's two obligations failed
        gives 18/20 as 18 verified, 2 failed, 0 undetermined."""
        leaves = self._leaves()
        outcomes = {}
        for leaf in leaves:
            if leaf["section"] != "C":
                continue
            outcomes[leaf["id"]] = {"outcome": VERIFIED}
        outcomes["C5.B"] = {"outcome": FAILED}
        card = score(leaves, outcomes)
        code = card["sections"]["C"]
        assert (code["verified"], code["failed"], code["undetermined"]) == (18, 2, 0)
        assert code["maximum"] == 20

    def test_an_unassessable_second_obligation_is_undetermined_and_not_a_half_penalty(self):
        leaves = self._leaves()
        outcomes = {leaf["id"]: {"outcome": VERIFIED} for leaf in leaves if leaf["section"] == "C"}
        del outcomes["C5.B"]
        card = score(leaves, outcomes)
        code = card["sections"]["C"]
        assert (code["verified"], code["failed"], code["undetermined"]) == (18, 0, 2)

    def test_replaying_the_same_outcomes_changes_nothing(self):
        leaves = self._leaves()
        outcomes = {"C1.A": {"outcome": VERIFIED}, "S3.B": {"outcome": FAILED}}
        assert score(leaves, outcomes) == score(allocate(default_profile()), dict(outcomes))


class TestTheScopeCountsLeavesAndTheBarWeighsPoints:
    def test_assessed_scope_counts_settled_leaves_and_keeps_undetermined_in_the_denominator(self):
        """Section 4: X / Y allocated leaf obligations with a settled verified or failed outcome.
        Undetermined leaves stay in Y. The count is unweighted; the bar is point-weighted."""
        leaves = allocate(default_profile())
        outcomes = {"C1.A": {"outcome": VERIFIED}, "C1.B": {"outcome": FAILED}, "S1.A": {"outcome": UNDETERMINED}}
        card = score(leaves, outcomes)
        assert card["assessed_scope"] == {"assessed": 2, "total": len(leaves)}
        assert card["verified"] == 2 and card["failed"] == 2

    def test_an_excluded_leaf_leaves_the_denominator_entirely(self):
        card = score(allocate(default_profile(exclude=["M2"])), {})
        assert card["assessed_scope"]["total"] == len(allocate(default_profile())) - 2


class TestExclusionsRedistributeAndTheProfileStillTotalsOneHundred:
    def test_an_excluded_criterion_redistributes_within_its_section(self):
        """Section 3.5: reference-genome selection can genuinely have no counterpart in a non-genomic
        analysis. Its four points go to M's remaining rows, proportionally; nothing leaves the section
        and the total is still 100."""
        leaves = allocate(default_profile(exclude=["M2"]))
        assert sum((l["weight"] for l in leaves), Fraction(0)) == 100
        computational = sum((l["weight"] for l in leaves if l["section"] == "M"), Fraction(0))
        assert computational == 20
        assert not [l for l in leaves if l["criterion"] == "M2"]
        assert sum((l["weight"] for l in leaves if l["criterion"] == "M1"), Fraction(0)) == 5

    def test_an_excluded_section_redistributes_among_the_remaining_sections(self):
        leaves = allocate(default_profile(exclude_sections=["R"]))
        assert sum((l["weight"] for l in leaves), Fraction(0)) == 100
        assert not [l for l in leaves if l["section"] == "R"]
        # 30 points shared in proportion to 20:15:15:20.
        assert sum((l["weight"] for l in leaves if l["section"] == "C"), Fraction(0)) == Fraction(200, 7)

    def test_the_profile_records_every_exclusion_with_its_rationale_and_source(self):
        profile = default_profile(
            exclude=[{"criterion": "M2", "rationale": "the analysis maps to no reference", "source": "methods p.4"}]
        )
        (recorded,) = profile["exclusions"]
        assert recorded["criterion"] == "M2"
        assert recorded["rationale"] == "the analysis maps to no reference"
        assert recorded["source"] == "methods p.4"
        assert profile["revision"] >= 1

    def test_excluding_everything_is_refused_rather_than_scored(self):
        with pytest.raises(ApplicabilityUncertain):
            default_profile(exclude_sections=["C", "S", "E", "M", "R"])

    def test_an_uncertain_exclusion_is_refused_so_a_hard_check_cannot_be_dropped(self):
        """Section 3.5: never improve the score by excluding a difficult check. An exclusion states
        what establishes it; one that cannot is not an exclusion, and the allocation stays."""
        with pytest.raises(ApplicabilityUncertain):
            default_profile(exclude=[{"criterion": "C5", "rationale": "", "source": None}])
