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


class TestTheDisplayedNumbersStillAddUp:
    def test_whole_numbers_display_without_a_decimal_point(self):
        from app.services.validation_rubric_v3 import display

        shown = display(score(allocate(default_profile()), {}))
        assert shown["verified"] == "0"
        assert shown["undetermined"] == "100"
        assert shown["parts"] == {"verified": 0.0, "failed": 0.0, "undetermined": 100.0}

    def test_the_displayed_parts_still_sum_to_the_profile_total(self):
        """Section 4: derive the components with a sum-preserving rounding rule. Three independently
        rounded thirds of 100 are 33.3 each and lose a tenth; the rule gives one of them the residue."""
        from app.services.validation_rubric_v3 import display

        leaves = [
            {"id": "a", "criterion": "C1", "section": "C", "obligation": "A", "unit": None, "weight": Fraction(100, 3)},
            {"id": "b", "criterion": "C1", "section": "C", "obligation": "B", "unit": None, "weight": Fraction(100, 3)},
            {"id": "c", "criterion": "C2", "section": "C", "obligation": "A", "unit": None, "weight": Fraction(100, 3)},
        ]
        card = score(leaves, {"a": {"outcome": VERIFIED}, "b": {"outcome": FAILED}})
        shown = display(card)
        assert sum(shown["parts"].values()) == 100.0

    def test_a_nonzero_residual_below_display_precision_is_shown_and_never_rounded_away(self):
        """Section 4: never round a residual away to claim 100% verified. "<0.1" says a small,
        genuinely nonzero amount is not verified."""
        from app.services.validation_rubric_v3 import display

        leaves = [
            {"id": "a", "criterion": "C1", "section": "C", "obligation": "A", "unit": None, "weight": Fraction(9999, 100)},
            {"id": "b", "criterion": "C1", "section": "C", "obligation": "B", "unit": None, "weight": Fraction(1, 100)},
        ]
        shown = display(score(leaves, {"a": {"outcome": VERIFIED}}))
        assert shown["verified"] == "99.9"
        assert shown["undetermined"] == "<0.1"
        assert shown["exact"]["undetermined"] == "1/100"

    def test_the_exact_values_are_preserved_beside_the_display(self):
        from app.services.validation_rubric_v3 import display

        leaves = allocate(default_profile(exclude_sections=["R"]))
        shown = display(score(leaves, {}))
        assert shown["exact"]["undetermined"] == "100"


class TestTheResultsSectionAllocatesAmongTheFindings:
    _INVENTORY = {
        "findings": [
            {"id": "F1", "required": [0, 1], "importance": {"category": "primary"}},
            {"id": "F2", "required": [2], "importance": {"category": "supporting"}},
            {"id": "F3", "required": [3], "importance": {"category": "technical"}},
        ]
    }

    def _allocation(self, **kw):
        from app.services.validation_rubric_v3 import result_allocation

        return result_allocation(self._INVENTORY, **kw)

    def test_a_technical_finding_receives_no_result_allocation(self):
        """Section 3.3: technical and descriptive findings get no R1/R2 weight; the technical checks
        that matter belong to the other criteria."""
        allocation = self._allocation()
        assert not [p for p in allocation["R1"] if p.get("finding") == "F3"]
        assert not [p for p in allocation["R2"] if p.get("finding") == "F3"]

    def test_primary_weighs_two_and_supporting_one_then_equally_among_the_claims(self):
        leaves = allocate(default_profile(), results=self._allocation())
        by_id = {leaf["id"]: leaf["weight"] for leaf in leaves}
        # F1 is primary (weight 2) with two required claims; F2 is supporting (weight 1) with one.
        assert by_id["R1.F1.0"] == Fraction(8) * Fraction(2, 3) / 2
        assert by_id["R1.F1.1"] == by_id["R1.F1.0"]
        assert by_id["R1.F2.2"] == Fraction(8) * Fraction(1, 3)
        assert sum((w for k, w in by_id.items() if k.startswith("R1.")), Fraction(0)) == 8

    def test_a_claim_shared_by_two_findings_is_measured_once_and_weighed_once(self):
        """Section 3.3: de-duplicate logically identical claims before allocation. Two findings that
        both require claim 2 must not create two R1 leaves for it, or a paper earns points twice for
        one measurement."""
        from app.services.validation_rubric_v3 import result_allocation

        inventory = {
            "findings": [
                {"id": "F1", "required": [0, 2], "importance": {"category": "primary"}},
                {"id": "F2", "required": [2], "importance": {"category": "primary"}},
            ]
        }
        allocation = result_allocation(inventory)
        assert len([p for p in allocation["R1"] if p["claim_index"] == 2]) == 1
        leaves = allocate(default_profile(), results=allocation)
        assert sum((l["weight"] for l in leaves if l["criterion"] == "R1"), Fraction(0)) == 8

    def test_the_workflows_split_the_execution_points_in_half_each(self):
        """Section 3.3: R3 allocates equally per workflow, half for demonstrated completion from the
        declared starting inputs and half for complete outputs and established requirements."""
        allocation = self._allocation(workflows=["nf-core/rnaseq", "nf-core/atacseq"])
        leaves = allocate(default_profile(), results=allocation)
        r3 = {leaf["id"]: leaf["weight"] for leaf in leaves if leaf["criterion"] == "R3"}
        assert sum(r3.values(), Fraction(0)) == 10
        assert set(r3.values()) == {Fraction(10, 4)}
        assert len(r3) == 4

    def test_an_unresolved_inventory_reserves_the_whole_of_r_as_undetermined(self):
        """Section 3.3: if no defensible allocation exists yet, all 30 R points are reserved and
        C/S/E/M still score. It is a legitimate untested 30, not a reason to withhold the other 70."""
        from app.services.validation_rubric_v3 import result_allocation

        allocation = result_allocation({"status": "pending"})
        leaves = allocate(default_profile(), results=allocation)
        reserved = [leaf for leaf in leaves if leaf.get("reserved")]
        assert {leaf["criterion"] for leaf in reserved} == {"R1", "R2", "R3"}
        assert sum((leaf["weight"] for leaf in reserved), Fraction(0)) == 30
        card = score(leaves, {leaf["id"]: {"outcome": VERIFIED} for leaf in leaves if leaf["section"] == "C"})
        assert card["verified"] == 20
        assert card["sections"]["R"]["undetermined"] == 30

    def test_adding_the_same_finding_twice_creates_no_extra_points(self):
        from app.services.validation_rubric_v3 import result_allocation

        doubled = {"findings": [*self._INVENTORY["findings"], *self._INVENTORY["findings"]]}
        leaves = allocate(default_profile(), results=result_allocation(doubled))
        assert sum((l["weight"] for l in leaves if l["section"] == "R"), Fraction(0)) == 30
