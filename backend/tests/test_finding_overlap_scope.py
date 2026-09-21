"""The owner's review, 2026-09-21: citation overlap is not proof of contradiction.

    "The new reconciliation pass demotes a positive whenever its citations are contained within a
    negative's citations, even if the judgments concern different facts. I reproduced a valid
    sample-preparation positive being demoted because a GO-threshold negative cited the same methods
    paragraph. Reconcile the actual assertions, scope, and consequences. Shared citations can
    identify candidates for review; they cannot decide the outcome."

Containment was already the narrower test that replaced plain overlap. It is still the wrong test on
its own: a paper's methods paragraph carries the sample preparation AND the enrichment threshold, so
a negative about the second contains a positive about the first and took it down.

Two judgments collide when they are about the same THING. Each answer now names the scope it is
about (`validation_judgment`, contract 3), and that is what the negatives' own deduplication has
always compared. The positive side compares it too: containment finds the candidates, the scope
decides, and a positive resting on evidence a negative disputes but about something else is reported
in TENSION rather than withdrawn.
"""

from app.services.validation_finding_overlap import reconcile_findings
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

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


class TestAPositiveAboutSomethingElseIsNotWithdrawn:
    def test_the_reproduced_case_keeps_its_point(self):
        """A GO-threshold negative and a sample-preparation positive citing the same paragraph."""
        found = reconcile_findings(
            {
                "M5.B": _negative("the GO-enrichment input selection for the bulk RNA-seq comparison", _METHODS),
                "S2.A": _positive("the material and preparation of the sequenced samples", ["p61"]),
            }
        )
        assert found["S2.A"]["outcome"] == VERIFIED
        assert found["S2.A"]["tension_with"] == "M5.B"

    def test_the_tension_says_which_negative_it_is_beside(self):
        found = reconcile_findings(
            {
                "M5.B": _negative("the GO-enrichment input selection", _METHODS),
                "S2.A": _positive("the material and preparation of the sequenced samples", ["p61"]),
            }
        )
        assert "M5.B" in found["S2.A"]["tension"]


class TestAPositiveAboutTheSameThingStillCannotStand:
    def test_a_positive_on_the_disputed_scope_is_withdrawn(self):
        found = reconcile_findings(
            {
                "M5.B": _negative("the GO-enrichment input selection for the bulk RNA-seq comparison", _METHODS),
                "M4.B": _positive("the GO-enrichment input selection for the bulk RNA-seq comparison", ["p61"]),
            }
        )
        assert found["M4.B"]["outcome"] == UNDETERMINED
        assert found["M4.B"]["contradicted_by"] == "M5.B"
        assert found["M4.B"]["withheld"]["outcome"] == VERIFIED

    def test_a_positive_resting_on_evidence_of_its_own_is_in_tension_not_withdrawn(self):
        found = reconcile_findings(
            {
                "M5.B": _negative("the GO-enrichment input selection", _METHODS),
                "M4.B": _positive("the GO-enrichment input selection", ["p61", "p99"]),
            }
        )
        assert found["M4.B"]["outcome"] == VERIFIED
        assert found["M4.B"]["tension_with"] == "M5.B"


class TestAnAnswerThatNamedNoScopeIsNotMatchedByItsBoilerplate:
    def test_two_judgments_that_named_no_scope_do_not_collide_on_passage_counts(self):
        """`judgment_from` used to fill `scope` with "12 supplied passages", so every judgment on a
        paper shared most of its scope words with every other. A scope bioAF did not read out of an
        answer settles nothing."""
        negative = {**_negative("", _METHODS), "scope": "2 supplied passages", "finding_scope": "2 supplied passages"}
        positive = {**_positive("", ["p61"]), "scope": "2 supplied passages", "scope_stated": False}
        found = reconcile_findings({"M5.B": negative, "S2.A": positive})
        assert found["S2.A"]["outcome"] == VERIFIED
        assert found["S2.A"].get("contradicted_by") is None
