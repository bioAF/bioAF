"""plan_8_6 section 9: what "0 negative" is allowed to mean.

A card showing zero negative points can mean either "bioAF examined the relevant evidence and found
no demonstrated problem" or "bioAF's checks were not given the evidence that would show one". Study
65 is the second and read as the first.

No new surface and no new vocabulary: the untested count and its reasons carry this. An obligation
left untested because its evidence was never supplied or never retrieved is named beside the score
in the same place a capability limit is, and the wording next to the counts says which of the two a
reader is looking at.
"""

from app.services.validation_rubric_v3 import allocate, default_profile, evidence_card

_PROFILE = default_profile()


def _leaves():
    return allocate(_PROFILE)


def _card(assessed):
    return evidence_card(profile=_PROFILE, leaves=_leaves(), assessed=assessed, capability_limits={})


_NOT_RETRIEVED = {
    "outcome": "undetermined",
    "rationale": "bioAF did not inspect the sources this absence would be about",
    "method": "model_assisted",
    "next_action": "retrieve and inspect the sources this obligation is about, then ask again",
    "coverage": {
        "sufficient": False,
        "unavailable": ["the supplementary bundle was too large to retrieve"],
        "truncated": False,
        "reason": "the supplementary bundle was too large to retrieve",
    },
}
_INSPECTED_AND_INCONCLUSIVE = {
    "outcome": "undetermined",
    "rationale": "the methods state a procedure but not enough of it to settle this",
    "method": "model_assisted",
    "coverage": {"sufficient": True, "unavailable": [], "truncated": False, "reason": ""},
}


class TestTheUntestedCountSaysWhyItIsUntested:
    def test_an_obligation_whose_evidence_was_never_retrieved_is_named(self):
        card = _card({"M1.B": _NOT_RETRIEVED})
        assert any(row["leaf"] == "M1.B" for row in card["evidence_limits"])
        assert any("too large" in row["reason"] for row in card["evidence_limits"])

    def test_an_obligation_bioaf_did_inspect_is_not_named_as_an_evidence_limit(self):
        card = _card({"M1.B": _INSPECTED_AND_INCONCLUSIVE})
        assert card["evidence_limits"] == []

    def test_a_truncated_packet_is_an_evidence_limit(self):
        judgment = {
            **_INSPECTED_AND_INCONCLUSIVE,
            "coverage": {
                "sufficient": True,
                "unavailable": [],
                "truncated": True,
                "reason": "the packet hit its budget",
            },
        }
        assert _card({"M1.B": judgment})["evidence_limits"]

    def test_the_points_at_stake_travel_with_each_row(self):
        row = next(r for r in _card({"M1.B": _NOT_RETRIEVED})["evidence_limits"] if r["leaf"] == "M1.B")
        assert row["points"] > 0
        assert row["section"] == "M"


class TestTheWordingBesideTheCountsSaysWhichZeroThisIs:
    def test_a_zero_negative_over_missing_evidence_says_so(self):
        card = _card({"M1.B": _NOT_RETRIEVED})
        assert str(card["display"]["failed"]) == "0"
        assert "not given" in card["explanation"] or "was not supplied" in card["explanation"]
        assert "evidence" in card["explanation"]

    def test_a_zero_negative_with_nothing_missing_is_not_qualified_that_way(self):
        card = _card({"M1.B": _INSPECTED_AND_INCONCLUSIVE})
        assert "not given" not in card["explanation"]

    def test_the_negative_count_is_never_a_finding_about_the_paper(self):
        card = _card({"M1.B": _NOT_RETRIEVED})
        assert "never a judgment about the paper" in card["explanation"]

    def test_the_existing_counts_are_unchanged(self):
        """No new surface and no new vocabulary: positive, untested and negative, as before."""
        card = _card({"M1.B": _NOT_RETRIEVED})
        assert [p["key"] for p in card["parts"]] == ["verified", "untested", "negative"]
        assert "positive points" in card["counts_label"]


class TestItReachesTheStoredCard:
    def test_the_projection_carries_the_evidence_limits(self):
        from app.services.validation_rubric_assessment import _serialize_leaves, _serialize_profile, card_from

        assessment = {
            "profile": _serialize_profile(_PROFILE),
            "leaves": _serialize_leaves(_leaves()),
            "outcomes": {"M1.B": _NOT_RETRIEVED},
            "revision": 1,
        }
        assert card_from(assessment)["evidence_limits"]
