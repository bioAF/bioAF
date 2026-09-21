"""plan_8_6 section 8: a justified negative is a finding, and the validator stops discarding it.

`validation_judgment` rejected any judgment whose rationale hedged its own outcome. The rule exists
for a real failure: a model answering `met` while its reasoning says it could not establish it. It
was applied to `unmet` as well, where "the paper does not state X" IS the finding. Six natural
phrasings of a justified negative were tested against the deployed validator and five became
`undetermined`; only a phrasing that happened to dodge the vocabulary survived.

The rule is per OUTCOME:

- for `met`, a rationale saying the evidence does not establish it is still a contradiction;
- for `unmet`, a demonstrated omission is a legitimate finding. A rationale that undercuts its OWN
  negative ("it may be stated elsewhere", "this could not be checked") is still a contradiction;
- an absence finding additionally needs the coverage record to establish that the sources the
  omission is about were actually inspected. An unavailable source or a truncated packet cannot
  support a paper-wide absence;
- positive evidence of a contradiction supports a narrowly scoped negative without inspecting
  unrelated sources.

These tests are written from how an assessor actually phrases an absence, not from how the regex
happens to read.
"""

import pytest

from app.services.validation_judgment import judgment_from
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

_PASSAGES = [
    {
        "id": "m1",
        "source": "the paper's methods (Statistical analysis)",
        "text": (
            "Differential expression was tested with DESeq2 and genes with an absolute log2 fold "
            "change greater than 2 were called significant."
        ),
    },
    {
        "id": "m2",
        "source": "the paper's methods (Statistical analysis)",
        "text": "Counts were modelled with a negative binomial fit and a Wald test.",
    },
]

# What the section 3 packet records when the sources an absence would be about were all inspected.
_COVERED = {
    "sufficient": True,
    "supplied": ["methods: statistical analysis", "methods: rna sequencing"],
    "unavailable": [],
    "deferred": [],
    "truncated": False,
    "reason": "every relevant methods section was supplied in full",
}

# The six natural phrasings of a justified negative, measured against the deployed validator.
SIX_PHRASINGS = [
    "The multiple-testing correction is not stated in the methods.",
    "A correction method is not specified anywhere in the paper.",
    "The paper does not say which correction was applied.",
    "There is no mention of a correction method.",
    "The threshold is ambiguous: two different cutoffs are given.",
    "The methods do not specify a correction.",
]


def _answer(**kw) -> dict:
    return {
        "outcome": "unmet",
        "rationale": "the multiple-testing correction is not stated in the methods",
        "citations": ["m1"],
        "impact": "a reader cannot reproduce the gene list without knowing the correction",
        "confidence": 0.8,
        **kw,
    }


class TestTheSixPhrasingsSurviveWhenTheirDefectIsEstablished:
    @pytest.mark.parametrize("rationale", SIX_PHRASINGS)
    def test_a_demonstrated_negative_fails_the_obligation(self, rationale):
        found = judgment_from("M4.A", _answer(rationale=rationale), passages=_PASSAGES, coverage=_COVERED)
        assert found["outcome"] == FAILED, rationale
        assert found["impact"]

    @pytest.mark.parametrize("rationale", SIX_PHRASINGS)
    def test_the_negative_keeps_the_citations_it_rests_on(self, rationale):
        found = judgment_from("M4.A", _answer(rationale=rationale), passages=_PASSAGES, coverage=_COVERED)
        assert found["evidence"]["citations"] == ["m1"]


class TestTheSameWordsBesideMetAreStillAContradiction:
    @pytest.mark.parametrize("rationale", SIX_PHRASINGS[:4])
    def test_met_beside_an_absence_stays_untested(self, rationale):
        found = judgment_from(
            "M4.A", _answer(outcome="met", rationale=rationale), passages=_PASSAGES, coverage=_COVERED
        )
        assert found["outcome"] == UNDETERMINED, rationale
        assert found["conflict"]

    def test_met_beside_an_assessor_that_could_not_tell_stays_untested(self):
        found = judgment_from(
            "M4.A",
            _answer(outcome="met", rationale="this cannot be established from the evidence supplied"),
            passages=_PASSAGES,
            coverage=_COVERED,
        )
        assert found["outcome"] == UNDETERMINED
        assert found["conflict"]

    def test_two_cutoffs_scoped_to_different_analyses_can_still_be_met(self):
        """Section 8: two thresholds that govern different analyses are not an ambiguity, and a
        rationale saying so must not be read as hedging itself."""
        found = judgment_from(
            "M4.B",
            _answer(
                outcome="met",
                rationale=(
                    "Two thresholds are given and each is unambiguously scoped: log2FC > 3 selects the "
                    "GO-enrichment input and log2FC > 2 defines the reported differential table."
                ),
            ),
            passages=_PASSAGES,
            coverage=_COVERED,
        )
        assert found["outcome"] == VERIFIED


class TestANegativeThatUnderminesItselfStaysUntested:
    @pytest.mark.parametrize(
        "rationale",
        [
            "The correction may be stated elsewhere in a supplement bioAF was not given.",
            "This could not be checked from the evidence in front of me.",
            "I cannot determine whether a correction was applied.",
            "It might be described in the supplementary methods, which were not supplied.",
        ],
    )
    def test_a_hedged_negative_is_a_contradiction(self, rationale):
        found = judgment_from("M4.A", _answer(rationale=rationale), passages=_PASSAGES, coverage=_COVERED)
        assert found["outcome"] == UNDETERMINED, rationale
        assert found["conflict"]


class TestAnAbsenceNeedsTheSourcesItIsAboutToHaveBeenInspected:
    def test_the_same_absence_wording_with_an_unretrieved_source_stays_untested(self):
        incomplete = {
            "sufficient": False,
            "supplied": ["methods: statistical analysis"],
            "unavailable": ["the supplementary methods could not be retrieved"],
            "deferred": [],
            "truncated": False,
            "reason": "the supplementary methods could not be retrieved",
        }
        found = judgment_from("M4.A", _answer(), passages=_PASSAGES, coverage=incomplete)
        assert found["outcome"] == UNDETERMINED
        assert "retriev" in found["rationale"] or "inspect" in found["rationale"]
        assert found["coverage"] == incomplete

    def test_a_truncated_packet_cannot_support_a_paper_wide_absence(self):
        truncated = {**_COVERED, "sufficient": True, "truncated": True, "reason": "the packet hit its budget"}
        found = judgment_from("M4.A", _answer(), passages=_PASSAGES, coverage=truncated)
        assert found["outcome"] == UNDETERMINED

    def test_a_demonstrated_contradiction_does_not_need_paper_wide_coverage(self):
        """Section 8: positive evidence of a contradiction supports a narrowly scoped negative.

        What makes it demonstrated is that the answer points at the two supplied passages that
        disagree, not that its rationale contains a word like "while" or "disagree": see
        `test_judgment_demonstrated_negative`.
        """
        incomplete = {
            "sufficient": False,
            "supplied": ["methods: statistical analysis"],
            "unavailable": ["the supplementary methods could not be retrieved"],
            "deferred": [],
            "truncated": False,
            "reason": "the supplementary methods could not be retrieved",
        }
        found = judgment_from(
            "M5.B",
            _answer(
                rationale=(
                    "The methods state an absolute log2 fold change greater than 3 for the GO-enrichment "
                    "input while the supplied deg_interpretation.py sets that threshold to 1, so the two "
                    "disagree on the same analysis step."
                ),
                citations=["m1", "m2"],
                scope="the GO-enrichment input selection",
                basis="contradiction",
                observations=[
                    {"citation": "m1", "states": "the paper sets the threshold above 3"},
                    {"citation": "m2", "states": "the supplied script sets that same threshold to 1"},
                ],
            ),
            passages=_PASSAGES,
            coverage=incomplete,
        )
        assert found["outcome"] == FAILED

    def test_a_materially_inappropriate_method_does_not_need_paper_wide_coverage(self):
        incomplete = {**_COVERED, "sufficient": False, "reason": "one supplement was not retrieved"}
        found = judgment_from(
            "M3.B",
            _answer(
                rationale=(
                    "A Wald test is applied to a single sample per condition, so the reported dispersion "
                    "has no replicate to estimate it from."
                ),
                citations=["m1", "m2"],
                scope="the differential expression comparison of Figure 3",
                basis="inappropriate",
                observations=[
                    {"citation": "m2", "states": "a Wald test is fitted to the counts"},
                    {"citation": "m1", "states": "each condition contributes one sample"},
                ],
            ),
            passages=_PASSAGES,
            coverage=incomplete,
        )
        assert found["outcome"] == FAILED

    def test_a_judgment_records_the_coverage_it_was_accepted_under(self):
        found = judgment_from("M4.A", _answer(), passages=_PASSAGES, coverage=_COVERED)
        assert found["coverage"] == _COVERED


class TestTheSystemPromptSaysWhatUnmetIs:
    def test_unmet_is_more_than_an_absent_item(self):
        from app.services.validation_judgment import build_request

        system = build_request("M4.A", passages=_PASSAGES)["system"].lower()
        assert "contradiction" in system
        assert "inappropriate" in system or "unsuitable" in system

    def test_an_unmet_answer_is_asked_for_its_scope_and_consequence(self):
        from app.services.validation_judgment import build_request

        system = build_request("M4.A", passages=_PASSAGES)["system"].lower()
        assert "scope" in system
        assert "impact" in system or "consequence" in system

    def test_an_absence_is_not_invited_where_the_evidence_was_not_inspected(self):
        from app.services.validation_judgment import build_request

        system = build_request("M4.A", passages=_PASSAGES)["system"].lower()
        assert "unmet" in system and "cannot_establish" in system
