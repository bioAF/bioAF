"""The owner, 2026-09-21: one demonstrated failure must not be several deductions.

"This discrepancy appears under C5.B, M1.B, and M5.B. Multiple deductions require distinct
demonstrated failures and consequences, not repeated wording about the same mismatch."

And the other direction: "M3.B awards positive credit for statistical-design appropriateness without
resolving this same concern [S5.A's]. Those judgments need reconciliation."

The obligations are judged one per request, which is a design invariant, so neither can be seen from
inside a single judgment. This is the pass that reconciles them afterwards, deterministically, from
what each one CITED rather than from what it happened to say.
"""

from app.services.validation_finding_overlap import reconcile_findings
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED


def _failed(citations, *, scope, rationale="the supplied code disagrees with the paper", impact="x"):
    return {
        "outcome": FAILED,
        "rationale": rationale,
        "impact": impact,
        "finding_scope": scope,
        "evidence": {"citations": list(citations)},
        "method": "model_assisted",
    }


def _verified(citations, *, rationale="the design is appropriate"):
    return {
        "outcome": VERIFIED,
        "rationale": rationale,
        "evidence": {"citations": list(citations)},
        "method": "model_assisted",
    }


class TestOneFailureIsOneDeduction:
    def test_a_negative_whose_evidence_another_already_covers_stops_deducting(self):
        """Study 65's C5.B cited p61, supp3 and two lines of deg_interpretation.py, all of which
        M5.B also cited, for the same GO-threshold mismatch in the same script."""
        judgments = {
            "M5.B": _failed(
                ["p61", "p105", "supp:s3#112", "code:DEG/deg_interpretation.py#3", "code:DEG/deg_interpretation.py#4"],
                scope="GO enrichment as implemented in DEG/deg_interpretation.py",
            ),
            "C5.B": _failed(
                ["p61", "supp:s3#112", "code:DEG/deg_interpretation.py#3", "code:DEG/deg_interpretation.py#4"],
                scope="GO enrichment step of DEG/deg_interpretation.py",
            ),
        }
        found = reconcile_findings(judgments)
        assert found["M5.B"]["outcome"] == FAILED, "the obligation the finding is about keeps it"
        assert found["C5.B"]["outcome"] == UNDETERMINED
        assert found["C5.B"]["same_finding_as"] == "M5.B"
        assert "M5.B" in found["C5.B"]["rationale"]

    def test_the_duplicate_keeps_what_it_found_so_nothing_is_lost(self):
        """With identical evidence and identical scope neither obligation is better placed to own
        the finding, so which one keeps it is not asserted: exactly one does, and the other says so
        while keeping what it found."""
        judgments = {
            "M5.B": _failed(["p61", "code:a.py#3"], scope="the GO step"),
            "C5.B": _failed(["p61", "code:a.py#3"], scope="the GO step"),
        }
        found = reconcile_findings(judgments)
        deducting = [leaf for leaf, j in found.items() if j["outcome"] == FAILED]
        demoted = [leaf for leaf, j in found.items() if j.get("same_finding_as")]
        assert len(deducting) == 1
        assert len(demoted) == 1
        assert found[demoted[0]]["same_finding_as"] == deducting[0]
        assert found[demoted[0]]["withheld"]["outcome"] == FAILED
        assert found[demoted[0]]["withheld"]["rationale"]

    def test_two_negatives_on_different_evidence_both_stand(self):
        judgments = {
            "M5.B": _failed(["p61", "code:a.py#3"], scope="the GO step"),
            "C2.B": _failed(["code:b.py#1"], scope="the imports of b.py"),
        }
        found = reconcile_findings(judgments)
        assert found["M5.B"]["outcome"] == FAILED
        assert found["C2.B"]["outcome"] == FAILED

    def test_a_negative_that_shares_evidence_but_names_a_different_scope_stands(self):
        """Distinct failures may rest on the same passage. The scope is what tells them apart."""
        judgments = {
            "M5.B": _failed(["p61", "code:a.py#3"], scope="the GO enrichment step of a.py"),
            "M1.B": _failed(["p61", "code:a.py#3"], scope="single-cell preprocessing from Parse counts matrices"),
        }
        found = reconcile_findings(judgments)
        assert found["M5.B"]["outcome"] == FAILED
        assert found["M1.B"]["outcome"] == FAILED

    def test_a_measured_finding_outranks_a_reviewed_one_for_which_keeps_the_deduction(self):
        judgments = {
            "C5.B": {**_failed(["p61"], scope="the GO step"), "method": "model_assisted"},
            "M4.A": {**_failed(["p61"], scope="the GO step"), "method": "measurement"},
        }
        found = reconcile_findings(judgments)
        assert found["M4.A"]["outcome"] == FAILED
        assert found["C5.B"]["outcome"] == UNDETERMINED


class TestAPositiveCannotStandOnEvidenceANegativeContradicts:
    def test_credit_for_the_design_cannot_stand_beside_an_unresolved_design_negative(self):
        """S5.A failed on the line-confounded replicates while M3.B awarded credit for the
        statistical design on the same evidence. Both cannot be true."""
        judgments = {
            "S5.A": _failed(
                ["p61", "design:3", "GSE213156/GSM6573673"],
                scope="bulk RNA-seq differential expression of TF-overexpression samples",
                rationale="the two biological replicates are two different parental lines and no blocking is stated",
            ),
            "M3.B": _verified(
                ["p61", "design:3"],
                rationale="each named test is matched to its data type and to a stated replication unit",
            ),
        }
        found = reconcile_findings(judgments)
        assert found["S5.A"]["outcome"] == FAILED
        assert found["M3.B"]["outcome"] == UNDETERMINED
        assert found["M3.B"]["contradicted_by"] == "S5.A"

    def test_a_positive_on_unrelated_evidence_is_untouched(self):
        judgments = {
            "S5.A": _failed(["p61", "design:3"], scope="the replicate structure"),
            "M2.A": _verified(["p70"]),
        }
        found = reconcile_findings(judgments)
        assert found["M2.A"]["outcome"] == VERIFIED

    def test_the_positive_keeps_what_it_found(self):
        judgments = {
            "S5.A": _failed(["p61", "design:3"], scope="the replicate structure"),
            "M3.B": _verified(["p61", "design:3"]),
        }
        found = reconcile_findings(judgments)
        assert found["M3.B"]["withheld"]["outcome"] == VERIFIED


class TestItChangesNothingItNeedNot:
    def test_a_set_with_no_overlap_comes_back_as_it_was(self):
        judgments = {"M1.A": _verified(["p1"]), "M2.A": _verified(["p2"])}
        assert reconcile_findings(judgments) == judgments

    def test_an_empty_set_is_fine(self):
        assert reconcile_findings({}) == {}

    def test_a_judgment_with_no_citations_is_left_alone(self):
        judgments = {"M1.A": {"outcome": UNDETERMINED, "rationale": "nothing to judge"}}
        assert reconcile_findings(judgments) == judgments


class TestSharingAPassageIsNotRestingOnTheSameFact:
    """Found by running the reconciliation on study 65: a negative citing seven background passages
    demoted every positive that happened to cite two of them, and the score fell for the wrong
    reason. A positive is only withdrawn where it rests on NOTHING the negative did not already
    account for; where the two merely overlap, the tension is recorded and the score is left alone
    for a person to reconcile."""

    def test_a_positive_resting_on_more_than_the_negative_keeps_its_outcome(self):
        judgments = {
            "E2.B": _failed(
                ["p59", "p61", "p71", "p68", "p133", "p44", "p40"],
                scope="the combinatorial screen underwriting the sufficiency claim",
            ),
            "E1.B": _verified(["p48", "p49", "p50", "p59", "p61"]),
        }
        found = reconcile_findings(judgments)
        assert found["E1.B"]["outcome"] == VERIFIED
        assert found["E1.B"]["tension_with"] == "E2.B", "the conflict is recorded for a person"
        assert "E2.B" in found["E1.B"]["tension"]

    def test_a_positive_resting_on_nothing_more_is_still_withdrawn(self):
        judgments = {
            "S5.A": _failed(["p61", "design:3", "GSE213156/GSM6573673"], scope="the replicate structure"),
            "M3.B": _verified(["p61", "design:3"]),
        }
        found = reconcile_findings(judgments)
        assert found["M3.B"]["outcome"] == UNDETERMINED
        assert found["M3.B"]["contradicted_by"] == "S5.A"

    def test_one_negative_does_not_take_down_a_whole_report(self):
        judgments = {
            "E2.B": _failed(["p1", "p2", "p3", "p4", "p5"], scope="the screen"),
            **{f"X{i}.A": _verified([f"p{i}", "p1", "p2"]) for i in range(6, 12)},
        }
        found = reconcile_findings(judgments)
        assert all(found[f"X{i}.A"]["outcome"] == VERIFIED for i in range(6, 12))
