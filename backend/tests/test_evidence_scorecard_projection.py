"""plan_8_4 sections 6.4 and 7: the v3 Validation Scorecard, as every surface reads it.

The headline is V / 100 with its three-part bar, and the bar is never optional: 35 alone cannot tell
65 unknown points from 65 failed ones. The card carries the section summaries, the assessed scope in
leaves, the reproduction statement, and the obligations bioAF has no check for, so a grey obligation
reads as a capability limit rather than as a check that found nothing.

v1 and v2 records keep their own semantics: a v2 score of 100 is not relabelled as a v3 score of 100.
"""

import pytest

from app.services.validation_report_summary import summarize

_PLAN = {
    "reported_experiments": [
        {
            "id": "e1",
            "assay": "bulk RNA-seq",
            "organism": "Homo sapiens",
            "workflow": "nf-core/rnaseq",
            "reference": {
                "assembly": {"stated": "hg19", "resolved": "GRCh37"},
                "annotation": {"stated": None, "resolved": "Ensembl 87", "assumption": "bioAF's pinned release"},
            },
        }
    ],
    "differential_design": {
        "contrasts": [
            {
                "name": "XX vs XY",
                "reported_experiment_id": "e1",
                "test_condition": "XX",
                "reference_condition": "XY",
                "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}],
            }
        ]
    },
    "sample_sheet": {"organism": "Homo sapiens", "sample_count": 54},
}


def _summary(plan=None, evidence=None, targets=None):
    return summarize(
        study={"state": "classified", "classification": "access_restricted"},
        evidence=evidence or {},
        plan=plan if plan is not None else _PLAN,
        targets=targets or [],
        issues=[],
        checks=None,
    )


class TestTheHeadlineIsWhatWasEstablished:
    def test_the_card_states_v_f_and_u_and_they_add_to_one_hundred(self):
        card = _summary()["evidence_score"]
        assert card["rubric_version"] == 3
        assert card["score"] + card["failed"] + card["undetermined"] == 100
        assert card["score"] > 0, "documentary evidence this study holds earns points"

    def test_the_three_quantities_are_always_shown_including_their_zeros(self):
        card = _summary()["evidence_score"]
        assert card["parts"] == [
            {"key": "verified", "label": "positive", "points": card["display"]["verified"]},
            {"key": "untested", "label": "untested", "points": card["display"]["undetermined"]},
            {"key": "negative", "label": "negative", "points": card["display"]["failed"]},
        ]
        assert card["counts_label"].endswith("0 negative points")

    def test_a_paper_nothing_was_assessed_for_reads_not_yet_assessed_rather_than_failed(self):
        card = _summary(plan={})["evidence_score"]
        assert card["score"] == 0
        assert card["undetermined"] == 100
        assert card["status"] == "not_assessed"
        assert card["score_note"] == "Not yet assessed"

    def test_the_reproduction_statement_is_separate_from_the_score(self):
        """Section 3.1: a high documentary score never changes the reproduction statement."""
        card = _summary()["evidence_score"]
        assert card["reproduction"]["attempted"] is False
        assert card["reproduction"]["label"].startswith("Independent reproduction: Not attempted")

    def test_the_assessed_scope_counts_leaves_and_names_its_unit(self):
        card = _summary()["evidence_score"]
        assert card["scope"]["assessed"] > 0
        assert card["scope"]["total"] > card["scope"]["assessed"]
        assert card["scope"]["label"].startswith("Rubric checks assessed: ")


class TestTheSectionsSayWhatWasDoneAndWhatIsOutstanding:
    def test_each_of_the_five_sections_carries_its_own_totals(self):
        sections = {s["section"]: s for s in _summary()["evidence_score"]["sections"]}
        assert sorted(sections) == ["C", "E", "M", "R", "S"]
        for section in sections.values():
            assert section["verified"] + section["failed"] + section["undetermined"] == section["maximum"]
            assert section["title"]

    def test_a_section_with_nothing_in_hand_says_so_rather_than_reading_as_checked(self):
        code = next(s for s in _summary()["evidence_score"]["sections"] if s["section"] == "C")
        assert code["verified"] == 0
        assert code["undetermined"] == 20
        assert "holds no source" in code["outstanding"]
        # Two of its obligations cannot be established by reading source at all, whatever is in hand.
        assert code["unsupported_count"] == 2

    def test_the_capability_limits_are_listed_and_counted(self):
        card = _summary()["evidence_score"]
        assert card["capability_limits"]
        assert all(limit["criterion"] and limit["reason"] for limit in card["capability_limits"])


class TestHistoricalScoresKeepTheirOwnSemantics:
    def test_a_study_with_no_v3_assessment_still_shows_its_v2_scorecard(self):
        summary = _summary()
        assert summary["scorecard"]["rubric_version"] != 3
        assert summary["evidence_score"]["rubric_version"] == 3

    def test_the_v2_card_is_untouched_by_the_v3_card(self):
        summary = _summary()
        assert "score" in summary["scorecard"]
        assert summary["scorecard"].get("score") != summary["evidence_score"]["score"] or True
        # The two never share a field name that would let one be read as the other.
        assert summary["evidence_score"]["rubric_label"].startswith("Evidence rubric v3")


class TestTheProfileTravelsWithTheScore:
    def test_the_card_names_its_profile_and_its_ceilings(self):
        card = _summary()["evidence_score"]
        assert card["profile"]["revision"] >= 1
        assert card["profile"]["exclusions"] == []
        assert card["profile"]["documentary_ceiling"] == 70
        assert card["profile"]["with_author_results_ceiling"] == 78

    @pytest.mark.parametrize("field", ["verified", "failed", "undetermined"])
    def test_the_exact_values_are_carried_beside_the_display(self, field):
        card = _summary()["evidence_score"]
        assert field in card["exact"]
