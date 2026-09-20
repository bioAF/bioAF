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
        assert card["reproduction"]["label"].startswith("Independent reproduction: not attempted")

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


class TestTheCardSaysWhatWouldBeAssessedNext:
    """plan_8_4 section 6.4: show the expected checks and their approval requirements. A grey
    obligation with no stated way forward is indistinguishable from one nobody will ever assess."""

    def test_every_open_obligation_that_has_a_next_action_is_listed(self):
        card = _summary()["evidence_score"]
        assert card["next_checks"]
        for row in card["next_checks"]:
            assert row["leaf"] and row["action"] and row["points"] > 0

    def test_with_no_source_in_hand_the_next_step_is_to_get_the_source(self):
        card = _summary()["evidence_score"]
        rows = {row["leaf"]: row for row in card["next_checks"]}
        assert "retrieve" in rows["C1.A"]["action"]
        assert rows["C1.A"]["needs_approval"] is False

    def test_the_ones_that_need_an_approval_say_so_once_the_source_is_read(self):
        """Loading and resolving execute the source, so what stands between them and a point is an
        approval, which is something a person can give."""
        evidence = {
            "code_inspection": {
                "sources": [{"path": "a.py", "language": "python", "text": "x = 1\n"}],
                "manifests": [{"path": "requirements.txt", "text": "numpy==1.26.4\n"}],
            }
        }
        card = _summary(evidence=evidence)["evidence_score"]
        gated = {row["leaf"] for row in card["next_checks"] if row["needs_approval"]}
        assert gated >= {"C1.B", "C2.B"}

    def test_it_is_ordered_by_what_the_points_are_worth(self):
        rows = _summary()["evidence_score"]["next_checks"]
        assert [r["points"] for r in rows] == sorted((r["points"] for r in rows), reverse=True)

    def test_an_obligation_nothing_can_resolve_is_not_offered_as_a_next_check(self):
        """A capability limit is a statement about bioAF, not an action a person can take."""
        card = _summary()["evidence_score"]
        limits = {limit["leaf"] for limit in card["capability_limits"]}
        offered = {row["leaf"] for row in card["next_checks"] if not row["needs_approval"]}
        assert not (offered & limits)


class TestASectionExpandsIntoItsCriteria:
    """plan_8_4 section 7: a section expands into criterion evidence, its partial-credit allocation and
    the next action. "Verified" means the named obligation was established, not that the paper is
    proven, so each obligation is shown as itself. Model-assisted and human-assisted judgments are
    labelled distinctly from a measurement."""

    def _section(self, key, evidence=None):
        card = _summary(evidence=evidence)["evidence_score"]
        return next(s for s in card["sections"] if s["section"] == key)

    def test_each_criterion_shows_its_two_obligations_with_their_own_points(self):
        rows = {row["criterion"]: row for row in self._section("S")["criteria"]}
        assert rows["S1"]["points"] == 3
        assert [o["obligation"] for o in rows["S1"]["obligations"]] == ["A", "B"]
        assert all(o["points"] == 1.5 for o in rows["S1"]["obligations"])
        assert rows["S1"]["title"]

    def test_partial_credit_reads_as_two_named_obligations_not_a_half_rating(self):
        evidence = {"precompute_checks": {"species_matches": {"verdict": "mismatch", "detail": "Mus musculus"}}}
        row = next(r for r in self._section("S", evidence)["criteria"] if r["criterion"] == "S1")
        outcomes = {o["obligation"]: o["outcome"] for o in row["obligations"]}
        assert outcomes == {"A": "verified", "B": "failed"}
        assert row["verified"] == 1.5 and row["failed"] == 1.5

    def test_each_obligation_states_what_it_required_and_what_was_found(self):
        obligation = next(
            o for r in self._section("S")["criteria"] for o in r["obligations"] if o["leaf"] == "S1.A"
        )
        assert obligation["statement"]
        assert obligation["rationale"]

    def test_an_open_obligation_carries_its_next_action(self):
        obligation = next(
            o for r in self._section("S")["criteria"] for o in r["obligations"] if o["leaf"] == "S3.B"
        )
        assert obligation["outcome"] == "undetermined"
        assert obligation["next_action"]

    def test_how_an_obligation_was_assessed_is_labelled(self):
        obligation = next(
            o for r in self._section("S")["criteria"] for o in r["obligations"] if o["leaf"] == "S1.A"
        )
        assert obligation["method"] == "measurement"
        assert obligation["method_label"] == "Measured"

    def test_a_model_assisted_judgment_is_labelled_distinctly(self):
        from app.services.validation_rubric_v3 import allocate, default_profile, evidence_card

        assessed = {
            "E1.B": {
                "outcome": "verified",
                "rationale": "the fixation time and the stain are stated",
                "scope": "2 passages",
                "method": "model_assisted",
            }
        }
        card = evidence_card(profile=default_profile(), leaves=allocate(default_profile()), assessed=assessed)
        obligation = next(
            o
            for s in card["sections"]
            for r in s["criteria"]
            for o in r["obligations"]
            if o["leaf"] == "E1.B"
        )
        assert obligation["method_label"] == "Reviewed by a model"

    def test_a_human_assisted_judgment_is_labelled_distinctly(self):
        from app.services.validation_rubric_v3 import allocate, default_profile, evidence_card

        assessed = {"S5.B": {"outcome": "verified", "rationale": "a person recorded the units", "scope": "4 columns", "method": "human_assisted"}}
        card = evidence_card(profile=default_profile(), leaves=allocate(default_profile()), assessed=assessed)
        obligation = next(
            o for s in card["sections"] for r in s["criteria"] for o in r["obligations"] if o["leaf"] == "S5.B"
        )
        assert obligation["method_label"] == "Confirmed by a person"


class TestTheReproductionStatementSaysOnlyWhatIsEstablished:
    """Found on the deployed build: the statement read "Not attempted — EGAS00001003667: EGA lists 108
    fastq.gz file(s) for this dataset", which pairs "not attempted" with a sentence describing what the
    deposit HOLDS. That is the evidence for a capability, not a reason nothing was run, and beside
    "not attempted" it reads as though reproduction were possible and had been declined.

    It also carried an em-dash, which this repository does not use.
    """

    def test_it_states_the_status_and_invents_no_reason(self):
        card = _summary()["evidence_score"]
        assert card["reproduction"]["label"] == "Independent reproduction: not attempted"
        assert card["reproduction"]["reason"] is None

    def test_a_deposits_contents_never_become_a_reason_nothing_ran(self):
        evidence = {
            "capabilities": {
                "raw_data": {"value": "yes", "evidence": "EGA lists 108 fastq.gz files for this dataset"}
            }
        }
        card = _summary(evidence=evidence)["evidence_score"]
        assert "fastq" not in card["reproduction"]["label"]
        assert card["reproduction"]["attempted"] is False

    def test_what_bioaf_did_acquire_is_stated_as_that_and_not_as_an_attempt(self):
        card = _summary(evidence={"deposit": {"accession": "GSE1"}})["evidence_score"]
        assert card["reproduction"]["attempted"] is False
        assert "deposited files" in card["reproduction"]["label"]
        assert "acquired" in card["reproduction"]["label"]

    def test_an_attempt_that_ran_names_what_executed(self):
        from app.services.validation_report_summary import evidence_scorecard

        card = evidence_scorecard(
            study={"state": "classified"},
            evidence={},
            plan=_PLAN,
            claims=[],
            attempt={"status": "attempted", "executed": ["analysis pipeline run"], "acquired": []},
        )
        assert card["reproduction"]["attempted"] is True
        assert "analysis pipeline run" in card["reproduction"]["label"]

    def test_no_em_dash_reaches_the_card(self):
        for evidence in ({}, {"deposit": {"accession": "GSE1"}}):
            card = _summary(evidence=evidence)["evidence_score"]
            for value in (card["reproduction"]["label"], card["explanation"], card["counts_label"]):
                assert "—" not in value, value


class TestTheReadingOfAClaimReachesTheScore:
    """plan_8_5 section 3.3: M4.B rests on the normalized predicate, so the projection must carry it.

    The claim row already showed the predicate in words. Words are for a reader; an obligation needs
    the structured reading, and without it every paper's M4.B was settled by a field nothing sets.
    """

    _TARGET = {
        "id": 1,
        "claim_text": "1204 genes were downregulated",
        "claimed_value": 1204,
        "output_type": "gene_set_size",
        "direction": "down",
        "contrast_index": 0,
        "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05, "adjustment": "BH"}],
        "count_relation": "=",
    }

    def test_a_claim_carries_its_reading_and_not_only_its_words(self):
        claim = _summary(targets=[self._TARGET])["claims"][0]
        assert claim["predicate"], "the words a reader sees"
        detail = claim["predicate_detail"]
        assert detail["status"] == "resolved"
        assert detail["direction"] == "down"
        assert detail["orientation"] == "test_over_reference"
        assert detail["significance"]["kind"] == "padj"
        assert detail["significance"]["adjustment"] == "BH"

    def test_that_reading_is_what_verifies_the_decision_criteria(self):
        card = _summary(targets=[self._TARGET])["evidence_score"]
        methods = next(s for s in card["sections"] if s["section"] == "M")
        rows = {row["leaf"]: row for criterion in methods["criteria"] for row in criterion["obligations"]}
        assert rows["M4.B"]["outcome"] == "verified"

    def test_a_claim_reporting_on_no_comparison_carries_no_reading(self):
        claim = _summary(targets=[{"id": 2, "claim_text": "the QC passed", "contrast_index": None}])["claims"][0]
        assert claim["predicate_detail"] is None
