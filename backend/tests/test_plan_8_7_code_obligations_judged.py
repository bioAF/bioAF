"""plan_8_7 stage 1: C4.A and C3.B are judged in the paper's context, with the parser beside them.

The parser establishes what is in the source. The obligations ask whether the source covers the
steps THIS paper claims, and whether the environment it names could be rebuilt. Neither question is
answerable from the source alone, so both reach the configured assessor with the paper's methods,
the supplied code and the parser's own observations in front of it.
"""

import pytest

from app.services.validation_documentary_review import JUDGED_LEAVES, carried_evidence, packets_for
from app.services.validation_evidence_packets import CHECKS, selector_for

_METHODS = (
    "Reads were trimmed with Trim Galore 0.6.7, aligned with STAR 2.7.10a to GRCh38, quantified with "
    "featureCounts, and differential expression was called with DESeq2 1.34.0."
)
_SOURCE = {
    "path": "analysis.py",
    "language": "python",
    "text": "def main():\n    print('hello')\n\n\nif __name__ == '__main__':\n    main()\n",
}
_MANIFEST = {"path": "environment.txt", "text": "python 3.11\n"}


def _evidence():
    return {
        "paper_index": {
            "passages": [
                {"id": "p1", "kind": "methods", "section": "Methods", "text": _METHODS, "source": "the paper"},
            ]
        },
        "code_inspection": {"sources": [_SOURCE], "manifests": [_MANIFEST]},
    }


class TestBothObligationsAreAsked:
    @pytest.mark.parametrize("leaf", ["C3.B", "C4.A"])
    def test_the_obligation_is_one_the_documentary_review_judges(self, leaf):
        assert leaf in JUDGED_LEAVES

    @pytest.mark.parametrize("leaf", ["C3.B", "C4.A"])
    def test_it_has_its_own_selector_rather_than_falling_back(self, leaf):
        assert selector_for(leaf) is not None


class TestTheParsersObservationsTravelWithTheQuestion:
    def test_what_the_parser_established_is_carried_as_evidence(self):
        rows = carried_evidence(evidence=_evidence(), plan={})["rows"]
        checks = [r for r in rows if r["kind"] == CHECKS]
        assert checks, "the parser's own observations must reach the assessor that judges the obligation"
        assert any("analysis.py" in r["text"] for r in checks)

    def test_the_entry_point_observation_names_itself_as_an_observation(self):
        rows = carried_evidence(evidence=_evidence(), plan={})["rows"]
        text = " ".join(r["text"] for r in rows if r["kind"] == CHECKS)
        assert "entry point" in text.lower()
        assert "python 3.11" in text or "runtime" in text.lower()

    def test_no_code_in_hand_carries_no_check_observations(self):
        rows = carried_evidence(evidence={"paper_index": {"passages": []}}, plan={})["rows"]
        assert not [r for r in rows if r["kind"] == CHECKS]


class TestThePacketsCarryTheClaimedStepsAndTheCode:
    def test_c4_a_is_shown_the_methods_the_paper_claims(self):
        packet = packets_for(evidence=_evidence(), plan={}, leaves=("C4.A",))["C4.A"]
        assert any(p["id"] == "p1" for p in packet["passages"])

    def test_c4_a_is_shown_the_supplied_source(self):
        packet = packets_for(evidence=_evidence(), plan={}, leaves=("C4.A",))["C4.A"]
        assert any(str(p["id"]).startswith("code:") for p in packet["passages"])

    def test_c3_b_is_shown_the_manifest_and_the_parsers_reading_of_it(self):
        packet = packets_for(evidence=_evidence(), plan={}, leaves=("C3.B",))["C3.B"]
        ids = [str(p["id"]) for p in packet["passages"]]
        assert any(i.startswith("code:environment.txt") for i in ids)
        assert any(i.startswith("check:") for i in ids)

    def test_a_paper_with_no_code_leaves_both_packets_unable_to_report_an_absence(self):
        """An obligation about the supplied code cannot report that the code omits something when
        bioAF holds none of it."""
        packets = packets_for(evidence={"paper_index": {"passages": []}}, plan={}, leaves=("C3.B", "C4.A"))
        for leaf in ("C3.B", "C4.A"):
            assert packets[leaf]["coverage"]["sufficient"] is False
