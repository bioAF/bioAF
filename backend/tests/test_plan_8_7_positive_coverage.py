"""plan_8_7 stage 1: sufficiency is validated for positives too, not only after a model hedges.

    "Validate evidence sufficiency before final acceptance for positive and negative judgments, not
    only after a model returns uncertainty."

The negative side has had this since plan_8_6: an absence is a claim about what was inspected. The
positive side is the same claim in the other direction wherever the obligation's subject is a source
bioAF did not get. M5.B asks whether the supplied code and the methods agree on the consequential
parameters; answered `met` on a paper whose repository never arrived, it is certainty about a file
nobody opened.

The rule is structural and narrow: only the source kinds an obligation DEPENDS on beyond the paper's
own text (its deposit, its code) hold a positive. An unretrieved attachment does not withhold every
positive in the report, which would be the "make every difficult check grey" failure plan_8_7 forbids.
"""

from app.services.validation_judgment import judgment_from
from app.services.validation_rubric_v3 import UNDETERMINED, VERIFIED

_PASSAGES = [{"id": "p1", "text": "Counts were filtered at 500 genes per cell.", "source": "the paper"}]


def _met(outcome="met"):
    return {
        "outcome": outcome,
        "rationale": "the methods state the filtering the analysis applied",
        "citations": ["p1"],
        "scope": "the single-cell preprocessing",
        "confidence": 0.8,
    }


def _coverage(**kw):
    base = {
        "packet_version": 2,
        "leaf": "M5.B",
        "sufficient": True,
        "supplied": ["Methods"],
        "unavailable": [],
        "truncated": False,
        "inspection_complete": True,
        "uninspected": 0,
        "needs_unavailable": [],
    }
    return {**base, **kw}


class TestAPositiveAboutASourceBioafNeverGotIsHeld:
    def test_it_stays_undetermined_with_what_it_found_withheld(self):
        found = judgment_from(
            "M5.B",
            _met(),
            passages=_PASSAGES,
            coverage=_coverage(
                sufficient=False,
                unavailable=["bioAF holds no source code for this paper"],
                needs_unavailable=["code"],
            ),
        )
        assert found["outcome"] == UNDETERMINED
        assert found["withheld"]["outcome"] == "met"
        assert "code" in found["rationale"]

    def test_the_next_action_names_the_source_to_get(self):
        found = judgment_from(
            "M5.B",
            _met(),
            passages=_PASSAGES,
            coverage=_coverage(sufficient=False, unavailable=["no code"], needs_unavailable=["code"]),
        )
        assert found["next_action"]


class TestItDoesNotGreyOutEveryPositive:
    def test_an_unretrieved_attachment_does_not_hold_a_positive(self):
        """`text` and `supplements` are needed by every obligation. Holding every positive on one
        missing attachment is the "make every difficult check grey" failure the plan forbids, so a
        real packet never records one as a need this rule reads."""
        from app.services.validation_evidence_packets import packet_for

        packet = packet_for(
            "M1.A",
            index={"passages": [{"id": "p1", "kind": "methods", "section": "Methods", "text": "Counts were filtered."}]},
            limitations=[{"needs": "supplements", "reason": "2 attachments were not retrieved"}],
        )
        assert packet["coverage"]["needs_unavailable"] == []
        assert packet["coverage"]["sufficient"] is False, "the absence side still sees it"
        found = judgment_from("M1.A", _met(), passages=_PASSAGES, coverage=packet["coverage"])
        assert found["outcome"] == VERIFIED

    def test_a_complete_packet_accepts_the_positive(self):
        assert judgment_from("M5.B", _met(), passages=_PASSAGES, coverage=_coverage())["outcome"] == VERIFIED

    def test_no_coverage_record_makes_no_claim_either_way(self):
        assert judgment_from("M5.B", _met(), passages=_PASSAGES, coverage=None)["outcome"] == VERIFIED

    def test_a_budget_truncation_does_not_hold_a_positive(self):
        """A positive rests on the passage that states it. Another paragraph nobody read does not
        unstate it, which is exactly the asymmetry between a positive and an absence."""
        found = judgment_from(
            "M5.B", _met(), passages=_PASSAGES, coverage=_coverage(truncated=True, inspection_complete=False)
        )
        assert found["outcome"] == VERIFIED


class TestThePacketRecordsWhichNeedWasMissing:
    def test_the_coverage_names_the_source_kinds_it_could_not_get(self):
        from app.services.validation_evidence_packets import packet_for

        packet = packet_for(
            "M5.B",
            index={"passages": [{"id": "p1", "kind": "methods", "section": "Methods", "text": "Code is on GitHub."}]},
            limitations=[{"needs": "code", "reason": "bioAF holds no source code for this paper"}],
        )
        assert packet["coverage"]["needs_unavailable"] == ["code"]
