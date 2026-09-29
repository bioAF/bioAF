"""The owner, 2026-09-21: one demonstrated failure must not be several deductions.

"This discrepancy appears under C5.B, M1.B, and M5.B. Multiple deductions require distinct
demonstrated failures and consequences, not repeated wording about the same mismatch."

And the other direction: "M3.B awards positive credit for statistical-design appropriateness without
resolving this same concern [S5.A's]. Those judgments need reconciliation."

The obligations are judged one per request, which is a design invariant, so neither can be seen from
inside a single judgment. A pass afterwards has to reconcile them.

plan_8_7, agreed with the owner on 2026-09-27, moved the DECISION out of this module: citation and
scope overlap identify candidates for review and no longer decide contradiction, duplication or the
withdrawal of a supported finding. What each pair means is read by
`validation_semantic_reconciliation`, and this file now asserts what the deterministic half still
promises: every one of the owner's cases reaches that reading, and nothing else does.
"""

from app.services.validation_finding_overlap import candidate_groups, reconcile_findings, related
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


def _verified(citations, *, rationale="the design is appropriate", scope=""):
    return {
        "outcome": VERIFIED,
        "rationale": rationale,
        "scope": scope,
        "scope_stated": bool(scope),
        "evidence": {"citations": list(citations)},
        "method": "model_assisted",
    }


class TestTheOwnersDuplicateCasesReachReview:
    def test_two_negatives_on_the_same_evidence_are_offered_together(self):
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
        assert candidate_groups(judgments) == [["C5.B", "M5.B"]]

    def test_neither_of_them_is_demoted_by_the_grouping_itself(self):
        judgments = {
            "M5.B": _failed(["p61", "code:a.py#3"], scope="the GO step"),
            "C5.B": _failed(["p61", "code:a.py#3"], scope="the GO step"),
        }
        found = reconcile_findings(judgments)
        assert [j["outcome"] for j in found.values()] == [FAILED, FAILED]
        assert found["C5.B"]["rests_on_shared_evidence_with"] == ["M5.B"]

    def test_two_negatives_on_different_evidence_are_not_a_group(self):
        judgments = {
            "M5.B": _failed(["p61", "code:a.py#3"], scope="the GO step"),
            "C2.B": _failed(["code:b.py#1"], scope="the imports of b.py"),
        }
        assert candidate_groups(judgments) == []

    def test_a_negative_that_shares_evidence_and_names_a_different_scope_is_still_offered(self):
        """Distinct failures may rest on the same passage. plan_8_7: what tells them apart is what
        they MEAN, and that is read, not inferred from the words each scope happened to use."""
        judgments = {
            "M5.B": _failed(["p61", "code:a.py#3"], scope="the GO enrichment step of a.py"),
            "M1.B": _failed(["p61", "code:a.py#3"], scope="single-cell preprocessing from Parse counts matrices"),
        }
        assert candidate_groups(judgments) == [["M1.B", "M5.B"]]


class TestThePositiveAndNegativeCasesReachReviewToo:
    def test_the_design_positive_and_the_replicate_negative_are_offered_together(self):
        """S5.A failed on the line-confounded replicates while M3.B awarded credit for the
        statistical design on the same evidence."""
        judgments = {
            "S5.A": _failed(
                ["p61", "design:3", "GSE213156/GSM6573673"],
                scope="bulk RNA-seq differential expression of TF-overexpression samples",
                rationale="the two biological replicates are two different parental lines and no blocking is stated",
            ),
            "M3.B": _verified(
                ["p61", "design:3"],
                scope="bulk RNA-seq differential expression of the TF-overexpression samples",
                rationale="each named test is matched to its data type and to a stated replication unit",
            ),
        }
        assert candidate_groups(judgments) == [["M3.B", "S5.A"]]

    def test_a_positive_on_unrelated_evidence_is_not_offered_or_touched(self):
        judgments = {
            "S5.A": _failed(["p61", "design:3"], scope="the replicate structure"),
            "M2.A": _verified(["p70"]),
        }
        assert candidate_groups(judgments) == []
        assert reconcile_findings(judgments)["M2.A"]["outcome"] == VERIFIED

    def test_the_positive_keeps_its_outcome_until_something_reads_the_pair(self):
        judgments = {
            "S5.A": _failed(["p61", "design:3"], scope="the replicate structure"),
            "M3.B": _verified(["p61", "design:3"], scope="the replicate structure of the comparison"),
        }
        assert reconcile_findings(judgments)["M3.B"]["outcome"] == VERIFIED


class TestItChangesNoOutcomeAtAll:
    def test_a_set_with_no_overlap_comes_back_as_it_was(self):
        judgments = {"M1.A": _verified(["p1"]), "M2.A": _verified(["p2"])}
        assert reconcile_findings(judgments) == judgments

    def test_an_empty_set_is_fine(self):
        assert reconcile_findings({}) == {}

    def test_a_judgment_with_no_citations_is_left_alone(self):
        judgments = {"M1.A": {"outcome": UNDETERMINED, "rationale": "nothing to judge"}}
        assert reconcile_findings(judgments) == judgments

    def test_an_unsettled_obligation_has_no_proposition_to_reconcile(self):
        judgments = {
            "M1.A": {"outcome": UNDETERMINED, "rationale": "nothing", "evidence": {"citations": ["p1"]}},
            "M3.B": _failed(["p1"], scope="the design"),
        }
        assert candidate_groups(judgments) == []


class TestSharingAPassageIsWorthReadingNotDeciding:
    """Found by running the old reconciliation on study 65: a negative citing seven background
    passages demoted every positive that happened to cite two of them, and the score fell for the
    wrong reason. Sharing a passage now means the pair is read; it decides nothing by itself."""

    def test_a_positive_overlapping_a_broad_negative_keeps_its_outcome(self):
        judgments = {
            "E2.B": _failed(
                ["p59", "p61", "p71", "p68", "p133", "p44", "p40"],
                scope="the combinatorial screen underwriting the sufficiency claim",
            ),
            "E1.B": _verified(["p48", "p49", "p50", "p59", "p61"]),
        }
        found = reconcile_findings(judgments)
        assert found["E1.B"]["outcome"] == VERIFIED
        assert found["E1.B"]["rests_on_shared_evidence_with"] == ["E2.B"]

    def test_one_negative_does_not_take_down_a_whole_report(self):
        judgments = {
            "E2.B": _failed(["p1", "p2", "p3", "p4", "p5"], scope="the screen"),
            **{f"X{i}.A": _verified([f"p{i}", "p1", "p2"]) for i in range(6, 12)},
        }
        found = reconcile_findings(judgments)
        assert all(found[f"X{i}.A"]["outcome"] == VERIFIED for i in range(6, 12))

    def test_relatedness_is_shared_evidence_or_a_shared_named_scope(self):
        assert related(_verified(["p1"]), _failed(["p1"], scope="x")) is True
        assert related(
            _verified(["p1"], scope="the GO enrichment step"), _failed(["p9"], scope="the GO enrichment step")
        )
        assert related(_verified(["p1"]), _failed(["p9"], scope="x")) is False
