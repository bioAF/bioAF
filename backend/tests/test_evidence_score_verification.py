"""plan_8_4 section 9: the required verification, as behaviour.

Each class is one of the section's headings. The point of the list is that the ways a score can become
dishonest are specific and enumerable: a failure that should have been an unknown, a limitation of
bioAF's charged to the paper, a duplicate that bought a point, a difficult check quietly excluded, a
model's recollection cashed as evidence. Every one of those is a test here.
"""

from fractions import Fraction


from app.services.validation_rubric_evidence import assess_evidence, profile_for
from app.services.validation_rubric_v3 import (
    FAILED,
    UNDETERMINED,
    VERIFIED,
    allocate,
    default_profile,
    display,
    result_allocation,
    score,
)

_PLAN = {
    "reported_experiments": [
        {
            "id": "e1",
            "assay": "bulk RNA-seq",
            "organism": "Homo sapiens",
            "workflow": "nf-core/rnaseq",
            "reference": {"assembly": {"stated": "hg38", "resolved": "GRCh38"}, "annotation": {"stated": "v44", "resolved": "v44"}},
        }
    ],
    "differential_design": {
        "contrasts": [
            {
                "name": "a vs b",
                "reported_experiment_id": "e1",
                "test_condition": "a",
                "reference_condition": "b",
                "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}],
            }
        ]
    },
    "sample_sheet": {"organism": "Homo sapiens", "sample_count": 12},
}
_INVENTORY = {"findings": [{"id": "F1", "required": [0, 1], "importance": {"category": "primary"}}]}


def _card(evidence=None, claims=None, inventory=None, plan=None):
    plan = plan if plan is not None else _PLAN
    leaves = allocate(profile_for(plan=plan), results=result_allocation(inventory))
    return score(leaves, assess_evidence(plan=plan, evidence=evidence or {}, claims=claims or [], inventory=inventory))


class TestArithmetic:
    def test_the_three_totals_always_add_to_the_profile_total(self):
        card = _card()
        assert card["verified"] + card["failed"] + card["undetermined"] == 100

    def test_zero_verified_after_a_failure_is_not_the_same_as_zero_verified_before_any_check(self):
        nothing = _card()
        broken = _card(
            evidence={"precompute_checks": {"species_matches": {"verdict": "mismatch", "detail": "Mus musculus"}}}
        )
        assert nothing["failed"] == 0 and broken["failed"] > 0
        assert nothing["assessed_points"] < broken["assessed_points"]

    def test_replaying_the_same_evidence_produces_the_same_totals(self):
        assert _card() == _card()


class TestIsolation:
    def test_a_judge_that_failed_cannot_turn_another_obligation_into_a_failure(self):
        from app.services.validation_judgment import judgment_from

        failed_judge = judgment_from("E1.B", None, passages=[{"id": "p1", "text": "t"}])
        assessed = {**assess_evidence(plan=_PLAN, evidence={}, claims=[], inventory=None), "E1.B": failed_judge}
        card = score(allocate(default_profile()), assessed)
        assert card["failed"] == 0
        assert failed_judge["outcome"] == UNDETERMINED

    def test_a_parser_bioaf_does_not_have_cannot_fail_the_syntax_obligation(self):
        """plan_8_5 section 3.5 gave bioAF an R parser; Julia is a language it still cannot read, and
        a language with no parser leaves the obligation grey rather than failing the paper."""
        assessed = assess_evidence(
            plan=_PLAN,
            evidence={"code_inspection": {"sources": [{"path": "a.jl", "language": "julia", "text": "f(x) = 1"}]}},
            claims=[],
            inventory=None,
        )
        assert assessed["C1.A"]["outcome"] == UNDETERMINED

    def test_unavailable_controlled_data_deducts_nothing(self):
        evidence = {
            "capabilities": {
                "raw_data": {"value": "no", "evidence": "the reads are under controlled access"},
                "preprocessed_data": {"value": "no"},
            }
        }
        assert _card(evidence=evidence)["failed"] == 0

    def test_one_failure_leaves_every_other_established_obligation_standing(self):
        verified_without = {k for k, v in assess_evidence(plan=_PLAN, evidence={}, claims=[], inventory=None).items() if v["outcome"] == VERIFIED}
        with_failure = assess_evidence(
            plan=_PLAN,
            evidence={"precompute_checks": {"species_matches": {"verdict": "mismatch", "detail": "Mus musculus"}}},
            claims=[],
            inventory=None,
        )
        assert verified_without <= {k for k, v in with_failure.items() if v["outcome"] == VERIFIED} | {"S1.B"}


class TestScope:
    def test_listing_a_finding_twice_creates_no_extra_points(self):
        doubled = {"findings": [*_INVENTORY["findings"], *_INVENTORY["findings"]]}
        assert _card(inventory=doubled)["sections"]["R"]["maximum"] == _card(inventory=_INVENTORY)["sections"]["R"]["maximum"]

    def test_one_script_cannot_establish_a_claim_about_all_of_them(self):
        """Section 3.2: an obligation spanning several analysis units splits its weight across them, so
        assessing one leaves the others open rather than covering them."""
        leaves = allocate(default_profile(), units={"C1.A": ["script_a", "script_b"]})
        card = score(leaves, {"C1.A#script_a": {"outcome": VERIFIED}})
        assert card["sections"]["C"]["verified"] == 1
        assert card["sections"]["C"]["undetermined"] == 19

    def test_more_files_never_create_more_available_points(self):
        one = allocate(default_profile(), units={"C1.A": ["a"]})
        many = allocate(default_profile(), units={"C1.A": ["a", "b", "c", "d"]})
        assert sum((leaf["weight"] for leaf in one), Fraction(0)) == sum((leaf["weight"] for leaf in many), Fraction(0)) == 100

    def test_an_unestablished_result_scope_reserves_its_points_and_scores_the_rest(self):
        card = _card(inventory={"status": "pending"})
        assert card["sections"]["R"]["undetermined"] == 30
        assert card["verified"] > 0


class TestEvidence:
    def test_an_ambiguous_signed_or_absolute_filter_receives_no_credit(self):
        claims = [
            {"index": 0, "consistency": {"outcome": "unresolved", "filter_semantics": {"unresolved": True}}},
            {"index": 1, "consistency": None},
        ]
        assessed = assess_evidence(plan=_PLAN, evidence={}, claims=claims, inventory=_INVENTORY)
        assert assessed["R1.F1.0"]["outcome"] == UNDETERMINED
        assert assessed["M4.B"]["outcome"] == UNDETERMINED

    def test_a_comparison_that_could_not_be_bound_receives_no_credit(self):
        claims = [{"index": 0, "consistency": {"outcome": "not_checkable"}}, {"index": 1, "consistency": None}]
        assessed = assess_evidence(plan=_PLAN, evidence={}, claims=claims, inventory=_INVENTORY)
        assert assessed["R1.F1.0"]["outcome"] == UNDETERMINED

    def test_a_species_contradiction_is_a_failure_and_names_its_impact(self):
        assessed = assess_evidence(
            plan=_PLAN,
            evidence={"precompute_checks": {"species_matches": {"verdict": "mismatch", "detail": "Mus musculus"}}},
            claims=[],
            inventory=None,
        )
        assert assessed["S1.B"]["outcome"] == FAILED
        assert assessed["S1.B"]["impact"]


class TestRuntime:
    def test_static_parsing_neither_imports_nor_executes_the_source(self, tmp_path, monkeypatch):
        """Section 9: the parser reads; it never runs. A source whose import or execution would have a
        visible effect must produce none."""
        from app.services.validation_code_checks import assess_code

        marker = tmp_path / "ran"
        source = (
            "import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
            "raise SystemExit(1)\n"
        )
        found = assess_code(sources=[{"path": "danger.py", "language": "python", "text": source}], manifests=[])
        assert found["C1.A"]["outcome"] == VERIFIED
        assert not marker.exists()

    def test_generated_repair_code_cannot_earn_the_original_sources_points(self):
        """Section 3.3: generated replacement code does not establish the validity of the authors'."""
        from app.services.validation_code_checks import assess_code

        found = assess_code(
            sources=[{"path": "generated.py", "language": "python", "text": "x = 1\n", "generated": True}],
            manifests=[],
        )
        assert found["C1.A"]["outcome"] == UNDETERMINED
        assert "generated" in found["C1.A"]["rationale"]


class TestApplicability:
    def test_an_exclusion_can_never_rest_on_a_missing_artifact(self):
        profile = profile_for(plan={"reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq"}]})
        assert profile["exclusions"] == []

    def test_an_uncertain_exclusion_keeps_the_allocation(self):
        profile = profile_for(plan={"reported_experiments": [{"id": "e1", "assay": ""}]})
        assert "M2" in profile["weights"]

    def test_a_declared_exclusion_changes_the_maxima_predictably(self):
        profile = profile_for(plan={"reported_experiments": [{"id": "e1", "assay": "western blotting"}]})
        assert "M2" not in profile["weights"]
        assert sum(profile["sections"].values()) == 100


class TestDisplay:
    def test_the_displayed_parts_never_claim_more_than_the_exact_values(self):
        card = _card()
        shown = display(card)
        assert Fraction(shown["exact"]["verified"]) >= Fraction(str(shown["parts"]["verified"])) - Fraction(1, 10)
        assert sum(shown["parts"].values()) == 100.0


class TestSupersededEvidenceIsNotCurrentCredit:
    """plan_8_4 section 6.3: superseded evidence cannot remain current credit. A comparison the report
    marks pending re-evaluation was made under a reading, a binding or a predicate that no longer
    stands, and a point for it would be a point for an answer bioAF has withdrawn."""

    def test_a_comparison_pending_re_evaluation_earns_nothing(self):
        claims = [
            {"index": 0, "consistency": {"outcome": "pending_re_evaluation", "superseded": {"label": "an earlier binding"}}},
            {"index": 1, "consistency": {"outcome": "agree"}},
        ]
        assessed = assess_evidence(plan=_PLAN, evidence={}, claims=claims, inventory=_INVENTORY)
        assert assessed["R1.F1.0"]["outcome"] == UNDETERMINED
        assert assessed["R1.F1.1"]["outcome"] == VERIFIED

    def test_withdrawing_an_answer_lowers_the_score_and_the_reason_is_on_the_record(self):
        agreed = _card(claims=[{"index": 0, "consistency": {"outcome": "agree"}}], inventory=_INVENTORY)
        withdrawn = _card(
            claims=[{"index": 0, "consistency": {"outcome": "pending_re_evaluation"}}], inventory=_INVENTORY
        )
        assert withdrawn["verified"] < agreed["verified"]
        assert withdrawn["failed"] == agreed["failed"] == 0
