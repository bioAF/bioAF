"""plan_8_7 stage 2: one projection, six areas, one leading summary, the rubric behind them.

Every surface reads the projection, so this is where "one revision supplies current conclusions,
counts, actions, list and exports" is either true or not.
"""

import pytest

from app.services.validation_report_summary import summarize
from tests.replay import with_assessment

_PLAN = {"reported_experiments": [{"id": "e1", "assay": "bulk RNA-seq", "organism": "Homo sapiens"}]}

_HELD = {
    "interpretation_review": {
        "reviews": {
            "claim:1": {
                "outcome": "contradicted",
                "rationale": "two clones from different parent lines are treated as replicates",
                "inferential_step": "treating two parental lines as biological replicates",
                "impact": "the sufficiency claim rests on a confounded comparison",
                "reproduction": None,
            }
        },
        "scope": "1 stated conclusion, with no reproduction performed",
    },
    "retrieval_ledger": [
        {"id": "R1", "url": "https://x/supp.zip", "outcome": "too_large", "at": "2026-09-01T00:00:00Z"},
        {"id": "R2", "url": "https://x/supp.zip", "outcome": "retrieved", "at": "2026-09-02T00:00:00Z"},
    ],
}


_EVIDENCE = with_assessment(_HELD, plan=_PLAN)


def _summary():
    return summarize(
        study={"state": "classified", "classification": "inconclusive"},
        evidence=_EVIDENCE,
        plan=_PLAN,
        targets=[],
        issues=[],
        checks=None,
    )


class TestTheProjectionCarriesTheNewShape:
    def test_the_six_areas_are_on_it(self):
        assert [a["key"] for a in _summary()["areas"]] == [
            "data",
            "experimental_methods",
            "computational_methods",
            "code",
            "results",
            "interpretation",
        ]

    def test_the_leading_summary_is_on_it_with_no_score(self):
        found = _summary()["assessment_summary"]
        assert found["assessment_revision"] == 1
        assert "score" not in found

    def test_the_interpretation_review_reaches_its_area(self):
        interpretation = next(a for a in _summary()["areas"] if a["key"] == "interpretation")
        assert any("parent lines" in row["statement"] for row in interpretation["concerns"])

    def test_the_current_retrieval_state_is_separate_from_the_history(self):
        found = _summary()["current_retrieval"]
        assert found["failures"] == []
        assert found["historical_failures"] == 1


class TestOnlyOneCardLeads:
    def test_the_findings_scorecard_is_marked_secondary(self):
        found = _summary()
        assert found["scorecard"]["secondary"] is True

    def test_the_evidence_score_is_marked_secondary_too(self):
        """plan_8_7 section 6: the numerical rubric stays available as secondary expandable detail."""
        assert _summary()["evidence_score"]["secondary"] is True

    def test_the_evidence_score_keeps_its_counts_and_its_scope(self):
        card = _summary()["evidence_score"]
        assert card["counts_label"]
        assert card["scope"]["label"]

    def test_a_historical_report_with_no_v3_card_is_untouched(self):
        found = summarize(
            study={"state": "classified", "classification": "inconclusive"},
            evidence={},
            plan={},
            targets=[],
            issues=[],
            checks=None,
        )
        assert found["evidence_score"] is None
        assert found["scorecard"].get("secondary") is not True


class TestTheOldSectionsStillExist:
    def test_nothing_that_had_a_surface_lost_one(self):
        """plan_8_7 non-goals: no deleting useful safeguards to reduce line count. The four sections
        still carry the findings, the checks and the diagnostics a reader had before."""
        sections = _summary()["sections"]
        assert set(sections) == {"findings", "data", "checks", "diagnostics"}


@pytest.mark.parametrize("key", ["obligations_attempted", "obligations_conclusive", "findings_conclusive"])
def test_the_three_counts_are_all_present(key):
    assert key in _summary()["assessment_summary"]["counts"]
