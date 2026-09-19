"""plan_8_4 section 5: what bioAF may ask a model about a rubric obligation, and what it does with the answer.

The contract, stated as behaviour:

- one criterion-specific obligation per request, with the evidence it is to be judged on. Never a
  holistic rating of the paper, and never a 0-100 number from a model;
- a structured outcome, with citations that must be found in the evidence that was supplied;
- an unsupported or self-contradictory judgment is UNDETERMINED, not a guess. Quote matching alone
  does not prove that the quoted passage supports the assessment, so a citation that is present but
  says nothing about the obligation still does not verify it;
- model confidence is never a multiplier, and it never becomes a fraction of a point.
"""

import pytest

from app.services.validation_judgment import (
    JUDGMENT_OUTCOMES,
    JudgmentRefused,
    build_request,
    judgment_from,
)
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

_PASSAGES = [
    {
        "id": "p1",
        "source": "methods",
        "text": "Cells were fixed in 4% paraformaldehyde for 15 min at room temperature and stained with DAPI.",
    },
    {"id": "p2", "source": "methods", "text": "Standard procedures were used throughout."},
]


class TestTheRequestAsksOneCriterionSpecificQuestion:
    def test_it_names_the_obligation_and_supplies_the_evidence_it_is_judged_on(self):
        request = build_request("E1.B", passages=_PASSAGES)
        assert request["leaf"] == "E1.B"
        assert request["obligation"]
        assert [p["id"] for p in request["evidence"]] == ["p1", "p2"]

    def test_it_never_asks_for_a_score(self):
        request = build_request("E1.B", passages=_PASSAGES)
        blob = (request["system"] + request["payload"]).lower()
        assert "0-100" not in blob and "0 to 100" not in blob
        assert "score" not in blob
        assert set(request["schema"]["outcome"]) == set(JUDGMENT_OUTCOMES)

    def test_it_requires_a_citation_from_the_supplied_evidence(self):
        request = build_request("E1.B", passages=_PASSAGES)
        assert "cite" in request["system"].lower()
        assert "citations" in request["schema"]

    def test_an_obligation_that_is_not_a_rubric_leaf_is_refused(self):
        with pytest.raises(JudgmentRefused):
            build_request("E9.Z", passages=_PASSAGES)

    def test_a_request_with_no_evidence_is_refused_rather_than_asked(self):
        """A model asked to judge an obligation with nothing in front of it is being asked to recall,
        and recollection is not evidence."""
        with pytest.raises(JudgmentRefused):
            build_request("E1.B", passages=[])


class TestTheAnswerIsCheckedAgainstWhatWasSupplied:
    def _answer(self, **kw):
        return {
            "outcome": "met",
            "rationale": "the fixation time, the concentration and the stain are all stated",
            "citations": ["p1"],
            "confidence": 0.9,
            **kw,
        }

    def test_a_grounded_met_judgment_verifies_the_obligation(self):
        found = judgment_from("E1.B", self._answer(), passages=_PASSAGES)
        assert found["outcome"] == VERIFIED
        assert found["method"] == "model_assisted"
        assert found["evidence"]["citations"] == ["p1"]

    def test_a_grounded_unmet_judgment_fails_it_with_its_impact(self):
        found = judgment_from(
            "E1.B",
            self._answer(outcome="unmet", rationale="no fixation time is stated anywhere", impact="it cannot be repeated"),
            passages=_PASSAGES,
        )
        assert found["outcome"] == FAILED
        assert found["impact"]

    def test_a_citation_that_is_not_in_the_supplied_evidence_leaves_it_undetermined(self):
        found = judgment_from("E1.B", self._answer(citations=["p9"]), passages=_PASSAGES)
        assert found["outcome"] == UNDETERMINED
        assert "cite" in found["rationale"] or "citation" in found["rationale"]

    def test_a_judgment_with_no_citation_at_all_leaves_it_undetermined(self):
        found = judgment_from("E1.B", self._answer(citations=[]), passages=_PASSAGES)
        assert found["outcome"] == UNDETERMINED

    def test_an_outcome_the_contract_does_not_declare_leaves_it_undetermined(self):
        found = judgment_from("E1.B", self._answer(outcome="probably"), passages=_PASSAGES)
        assert found["outcome"] == UNDETERMINED

    def test_a_judgment_that_says_met_and_also_says_it_could_not_tell_is_undetermined(self):
        """Section 5: an unsupported or contradictory judgment stays undetermined, and the conflict is
        recorded rather than averaged away."""
        found = judgment_from(
            "E1.B",
            self._answer(rationale="the methods do not say, so this cannot be established from the text"),
            passages=_PASSAGES,
        )
        assert found["outcome"] == UNDETERMINED
        assert found["conflict"]

    def test_confidence_never_becomes_a_fraction_of_a_point(self):
        """Section 4: model confidence is not a multiplier."""
        high = judgment_from("E1.B", self._answer(confidence=0.99), passages=_PASSAGES)
        low = judgment_from("E1.B", self._answer(confidence=0.2), passages=_PASSAGES)
        assert high["outcome"] == low["outcome"] == VERIFIED
        assert "weight" not in high and "multiplier" not in high

    def test_the_judgment_records_the_model_and_the_check_version(self):
        found = judgment_from("E1.B", self._answer(), passages=_PASSAGES, model="claude-opus-5")
        assert found["assessor"]["model"] == "claude-opus-5"
        assert found["assessor"]["contract_version"]


class TestAFailedJudgeAffectsOnlyItsOwnObligation:
    def test_a_refusal_yields_one_undetermined_obligation_and_nothing_else(self):
        found = judgment_from("E1.B", None, passages=_PASSAGES)
        assert list(found) and found["outcome"] == UNDETERMINED
        assert found["next_action"]
