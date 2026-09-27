"""The owner's review, 2026-09-21: citation overlap is not proof of contradiction.

    "The new reconciliation pass demotes a positive whenever its citations are contained within a
    negative's citations, even if the judgments concern different facts. I reproduced a valid
    sample-preparation positive being demoted because a GO-threshold negative cited the same methods
    paragraph. Reconcile the actual assertions, scope, and consequences. Shared citations can
    identify candidates for review; they cannot decide the outcome."

Containment was the narrower test that replaced plain overlap, and comparing the scope each answer
named was the narrower one after that. plan_8_7, agreed with the owner on 2026-09-27, stopped
looking for a narrower deterministic test: two propositions collide over what they MEAN, and no
comparison of the words in a scope sentence reaches that. A method can be fully described and still
be the wrong method.

So this module finds the candidates and changes no outcome. What the pair means is read by
`validation_semantic_reconciliation` with the shared evidence in front of the configured model, and
its behaviour is accepted in `test_plan_8_7_semantic_reconciliation.py`.
"""

from app.services.validation_finding_overlap import candidate_groups, reconcile_findings
from app.services.validation_rubric_v3 import FAILED, VERIFIED

_METHODS = ["p61", "p62"]


def _negative(scope: str, citations: list[str]) -> dict:
    return {
        "outcome": FAILED,
        "rationale": "the stated threshold and the supplied script disagree",
        "method": "model_assisted",
        "scope": scope,
        "finding_scope": scope,
        "impact": "a reader cannot establish which threshold produced the reported terms",
        "evidence": {"citations": citations},
    }


def _positive(scope: str, citations: list[str]) -> dict:
    return {
        "outcome": VERIFIED,
        "rationale": "the material and its preparation are stated for every reported measurement",
        "method": "model_assisted",
        "scope": scope,
        "scope_stated": True,
        "evidence": {"citations": citations},
    }


class TestTheReproducedCaseKeepsItsPoint:
    def test_a_positive_about_something_else_is_not_withdrawn(self):
        """A GO-threshold negative and a sample-preparation positive citing the same paragraph."""
        found = reconcile_findings(
            {
                "M5.B": _negative("the GO-enrichment input selection for the bulk RNA-seq comparison", _METHODS),
                "S2.A": _positive("the material and preparation of the sequenced samples", ["p61"]),
            }
        )
        assert found["S2.A"]["outcome"] == VERIFIED

    def test_it_is_offered_for_reading_rather_than_decided(self):
        found = candidate_groups(
            {
                "M5.B": _negative("the GO-enrichment input selection", _METHODS),
                "S2.A": _positive("the material and preparation of the sequenced samples", ["p61"]),
            }
        )
        assert found == [["M5.B", "S2.A"]]


class TestAPositiveOnTheSameScopeIsAlsoOnlyACandidate:
    def test_it_keeps_its_outcome_until_the_pair_is_read(self):
        found = reconcile_findings(
            {
                "M5.B": _negative("the GO-enrichment input selection for the bulk RNA-seq comparison", _METHODS),
                "M4.B": _positive("the GO-enrichment input selection for the bulk RNA-seq comparison", ["p61"]),
            }
        )
        assert found["M4.B"]["outcome"] == VERIFIED
        assert found["M4.B"]["rests_on_shared_evidence_with"] == ["M5.B"]

    def test_a_positive_resting_on_evidence_of_its_own_is_offered_too(self):
        found = candidate_groups(
            {
                "M5.B": _negative("the GO-enrichment input selection", _METHODS),
                "M4.B": _positive("the GO-enrichment input selection", ["p61", "p99"]),
            }
        )
        assert found == [["M4.B", "M5.B"]]


class TestAnAnswerThatNamedNoScopeIsNotMatchedByItsBoilerplate:
    def test_two_judgments_that_named_no_scope_do_not_group_on_passage_counts(self):
        """`judgment_from` used to fill `scope` with "12 supplied passages", so every judgment on a
        paper shared most of its scope words with every other. A scope bioAF did not read out of an
        answer settles nothing, and it does not make two findings related either."""
        negative = {**_negative("", ["p1"]), "scope": "2 supplied passages", "finding_scope": "2 supplied passages"}
        positive = {**_positive("", ["p9"]), "scope": "2 supplied passages", "scope_stated": False}
        assert candidate_groups({"M5.B": negative, "S2.A": positive}) == []
