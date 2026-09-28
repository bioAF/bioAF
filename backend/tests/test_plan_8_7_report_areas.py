"""plan_8_7 stage 2: one assessment summary, six areas, and the numerical rubric behind them.

The owner's September 21 assessment found two prominent scorecards on the report, a stale retrieval
failure beside the resource that had been retrieved, and claim counts that disagreed with each other.
The report also had no shape a scientist could read: four engineering sections (Findings, Data and
code, Checks performed, Run diagnostics) rather than an account of the paper.

plan_8_7 section 3 names the six areas the report must answer, and section 6 says the summary leads,
the areas expand, rubric v3 is secondary detail, and the competing findings score leaves the headline
while its findings and checks stay.
"""

from app.services.validation_report_areas import AREAS, areas_for, assessment_summary

_ASSESSMENT = {
    "revision": 3,
    "at": "2026-09-27T00:00:00+00:00",
    "checker_version": 3,
    "outcomes": {
        "S1.A": {"outcome": "verified", "rationale": "the paper states the organism for every experiment"},
        "S2.A": {"outcome": "verified", "rationale": "the material is stated for every measurement"},
        "E1.A": {
            "outcome": "failed",
            "rationale": "the transfection volume is not stated",
            "impact": "a reader cannot repeat it",
        },
        "M1.A": {"outcome": "verified", "rationale": "the preprocessing steps are stated"},
        "M3.B": {
            "outcome": "failed",
            "rationale": "three harvests of one donor are treated as independent",
            "impact": "the reported significance is overstated",
        },
        "C1.A": {"outcome": "verified", "rationale": "the supplied source parses"},
        "C4.A": {
            "outcome": "undetermined",
            "rationale": "which claimed steps the source covers is not established",
            "next_action": "bind each claimed step to the script that performs it",
        },
        "R1": {"outcome": "undetermined", "rationale": "no comparison has run", "next_action": "approve a run"},
    },
    "leaves": [
        {"id": leaf, "criterion": leaf.partition(".")[0], "section": leaf[0], "weight": "1"}
        for leaf in ("S1.A", "S2.A", "E1.A", "M1.A", "M3.B", "C1.A", "C4.A", "R1")
    ],
}
_INTERPRETATION = {
    "reviews": {
        "claim:1": {
            "outcome": "contradicted",
            "rationale": "the two clones are different parent lines",
            "inferential_step": "treating two parental lines as biological replicates",
            "impact": "the sufficiency claim rests on a confounded comparison",
            "reproduction": None,
        }
    },
    "scope": "1 stated conclusion, with no reproduction performed",
}


def _projection(**kw):
    base = {
        "assessment": _ASSESSMENT,
        "interpretation_review": _INTERPRETATION,
        "resources": [],
        "artifacts": [],
        "retrieval_failures": [],
        "claims": [],
        "claim_counts": {},
        "code_sources": [],
        "comparisons": {"performed": False, "label": "No comparison was performed", "reason": "no run was approved"},
        "attempt": {"attempted": False},
    }
    return {**base, **kw}


class TestTheSixAreas:
    def test_the_areas_are_the_six_the_plan_names(self):
        assert [a["key"] for a in AREAS] == [
            "data",
            "experimental_methods",
            "computational_methods",
            "code",
            "results",
            "interpretation",
        ]

    def test_every_area_is_present_even_with_nothing_to_say(self):
        found = areas_for(_projection(assessment=None, interpretation_review=None))
        assert [a["key"] for a in found] == [a["key"] for a in AREAS]
        assert all(a["summary"] for a in found)

    def test_an_obligation_reaches_the_area_it_is_about(self):
        found = {a["key"]: a for a in areas_for(_projection())}
        assert any("organism" in row["statement"] for row in found["data"]["supported"])
        assert any("transfection" in row["statement"] for row in found["experimental_methods"]["concerns"])
        assert any("harvests" in row["statement"] for row in found["computational_methods"]["concerns"])
        assert any("parses" in row["statement"] for row in found["code"]["supported"])

    def test_an_area_carries_supported_concerns_and_untested_together(self):
        """plan_8_7 section 3: "Do not force a whole section into one pass/fail label.\""""
        code = next(a for a in areas_for(_projection()) if a["key"] == "code")
        assert code["supported"] and code["untested"]
        assert "outcome" not in code

    def test_untested_work_names_its_cause_and_its_next_action(self):
        code = next(a for a in areas_for(_projection()) if a["key"] == "code")
        row = next(r for r in code["untested"] if "claimed steps" in r["statement"])
        assert row["next_action"]

    def test_the_interpretation_area_comes_from_the_interpretation_review(self):
        found = next(a for a in areas_for(_projection()) if a["key"] == "interpretation")
        assert any("parent lines" in row["statement"] for row in found["concerns"])
        assert found["concerns"][0]["inferential_step"]

    def test_the_code_area_carries_the_required_follow_up_and_its_blocker(self):
        """plan_8_7 section 3: silence about a published implementation reads as a paper that
        published none, which is a different and false statement."""
        found = next(
            a
            for a in areas_for(
                _projection(
                    code_followup={
                        "followups": [
                            {
                                "unit": "code:sim.jl",
                                "action": "blocked",
                                "missing": "runtime",
                                "reason": "bioAF supplies no runtime for julia",
                                "source": {"commit_sha": "abc"},
                            }
                        ],
                        "blocked": [{"action": "attempt_bounded_check", "reason": "no isolated identity"}],
                    }
                )
            )
            if a["key"] == "code"
        )
        assert found["followup"][0]["missing"] == "runtime"
        assert found["followup_blocked"][0]["reason"] == "no isolated identity"

    def test_a_paper_with_no_follow_up_recorded_carries_an_empty_list(self):
        found = next(a for a in areas_for(_projection()) if a["key"] == "code")
        assert found["followup"] == []

    def test_reproduction_depth_is_its_own_statement_in_the_results_area(self):
        found = next(a for a in areas_for(_projection()) if a["key"] == "results")
        assert found["reproduction"]["attempted"] is False
        assert "no run" in found["reproduction"]["reason"]


class TestOneSummaryLeads:
    def test_it_names_the_consequential_concerns_and_what_is_untested(self):
        found = assessment_summary(_projection())
        assert found["concerns"]
        assert found["untested_count"] >= 1
        assert found["supported_count"] >= 1

    def test_it_carries_no_score_of_its_own(self):
        found = assessment_summary(_projection())
        assert "score" not in found
        assert "headline" not in found

    def test_it_says_which_revision_it_is_reading(self):
        assert assessment_summary(_projection())["assessment_revision"] == 3

    def test_with_no_assessment_it_says_so_rather_than_inventing_one(self):
        found = assessment_summary(_projection(assessment=None, interpretation_review=None))
        assert found["assessment_revision"] is None
        assert found["reason"]


class TestTheCountsAgree:
    def test_attempted_conclusive_and_findings_are_counted_separately(self):
        counts = assessment_summary(_projection())["counts"]
        assert counts["obligations_attempted"] == 8
        assert counts["obligations_conclusive"] == 6
        assert counts["findings_conclusive"] == 2

    def test_an_unresolved_comparison_that_finished_is_not_an_agreement(self):
        found = areas_for(
            _projection(
                comparisons={"performed": True, "label": "1 comparison", "reason": None},
                attempt={"attempted": True},
                measured=[{"metric": "peaks", "paper": 7389, "ours": 4054, "agrees": None}],
            )
        )
        results = next(a for a in found if a["key"] == "results")
        assert results["reproduction"]["agreed"] is False
        assert results["reproduction"]["unresolved"] is True
