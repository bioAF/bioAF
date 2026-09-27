"""plan_8_7 stage 1: keyword absence is not irrelevance, and uninspected context is not inspected.

The owner's September 21 assessment:

    "A methods sentence about repeated observations is omitted while coverage is sufficient."

M3.B asks whether the statistical design accounts for the structure of the data. The paper said each
donor was harvested three times; the selector matched none of those words, so the sentence ranked
zero, was passed over for paragraphs that matched `DESeq2`, and the coverage record reported the
packet as a complete inspection anyway.

Two separate repairs:

- **eligibility is the section, not the vocabulary.** A methods paragraph is candidate evidence for a
  question about the methods however it is worded. Ranking still reads first.
- **what was not carried is recorded as not carried.** A packet that deferred eligible context says
  so, and an absence finding cannot rest on a search that did not finish.
"""

from app.services.validation_evidence_packets import packet_for
from app.services.validation_judgment import coverage_supports_absence

# Deliberately in none of M3's vocabulary: no "replicate", "n =", "batch", "model", "paired", "test".
_REPEATED = (
    "Every participant contributed three separate harvests taken a week apart, and all of the "
    "resulting libraries were analysed together."
)
_MATCHES = "Differential expression was tested with DESeq2 using a Wald test and shrunken dispersions."


def _index(*paragraphs, kind="methods", section="Methods"):
    return {
        "passages": [
            {"id": f"p{i}", "kind": kind, "section": section, "text": text, "source": "the paper"}
            for i, text in enumerate(paragraphs, start=1)
        ]
    }


class TestAMethodsSentenceReachesAMethodsQuestion:
    def test_a_sentence_matching_no_keyword_still_reaches_the_packet(self):
        packet = packet_for("M3.B", index=_index(_MATCHES, _REPEATED))
        assert [p["id"] for p in packet["passages"]] == ["p1", "p2"]

    def test_it_reaches_it_when_the_sections_are_reordered(self):
        packet = packet_for("M3.B", index=_index(_REPEATED, _MATCHES))
        assert "p1" in [p["id"] for p in packet["passages"]]

    def test_the_relevant_paragraph_is_still_read_first_under_a_tight_budget(self):
        """Ranking decides what a small budget spends itself on; eligibility decides what is offered."""
        packet = packet_for("M3.B", index=_index(_REPEATED, _MATCHES), budget_chars=len(_MATCHES) + 5)
        assert [p["id"] for p in packet["passages"]] == ["p2"]

    def test_a_passage_the_obligation_excludes_is_still_dropped(self):
        bench = "Cells were cultured in mTeSR Plus on Matrigel-coated plates and passaged with EDTA."
        packet = packet_for("M1.B", index=_index(_MATCHES, bench))
        assert "p2" not in [p["id"] for p in packet["passages"]]
        assert packet["coverage"]["excluded_irrelevant"] == 1


class TestUninspectedContextIsRecordedAsUninspected:
    def test_a_packet_that_carried_everything_says_the_inspection_finished(self):
        packet = packet_for("M3.B", index=_index(_MATCHES, _REPEATED))
        assert packet["coverage"]["inspection_complete"] is True
        assert packet["coverage"]["uninspected"] == 0

    def test_a_packet_the_budget_cut_says_what_it_did_not_read(self):
        packet = packet_for("M3.B", index=_index(_MATCHES, _REPEATED), budget_chars=len(_MATCHES) + 5)
        coverage = packet["coverage"]
        assert coverage["inspection_complete"] is False
        assert coverage["uninspected"] == 1
        assert "not inspected" in coverage["reason"]

    def test_the_widened_packet_reports_its_own_completed_inspection(self):
        packet = packet_for("M3.B", index=_index(_MATCHES, _REPEATED), budget_chars=len(_MATCHES) + 5)
        assert packet["expanded_coverage"]["inspection_complete"] is True


class TestAnAbsenceCannotRestOnAnUnfinishedSearch:
    def test_an_incomplete_inspection_refuses_an_absence_finding(self):
        packet = packet_for("M3.B", index=_index(_MATCHES, _REPEATED), budget_chars=len(_MATCHES) + 5)
        supported, why = coverage_supports_absence(packet["coverage"])
        assert supported is False
        assert "inspect" in why

    def test_a_completed_inspection_supports_one(self):
        packet = packet_for("M3.B", index=_index(_MATCHES, _REPEATED))
        assert coverage_supports_absence(packet["coverage"]) == (True, "")
