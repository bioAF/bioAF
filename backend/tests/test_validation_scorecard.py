"""plan_8 sections 1 and 5: the Validation Scorecard's two metrics, computed once by a pure builder.

The overall score is agreement among conclusively assessed findings, primary findings weighted twice
as much as supporting ones. The assessed scope is an unweighted count of scoreable findings that
received such an assessment. Neither is a probability, a grade, or a verdict on the whole paper.
"""

import pytest

from app.services.validation_scorecard import (
    BLOCKED,
    DISCREPANCY,
    INCONCLUSIVE,
    NOT_ATTEMPTED,
    SUPPORTED,
    UNRESOLVED,
    ScorecardInvariantError,
    build_scorecard,
    compact_scorecard,
    display_score,
    weight_for,
)


def _inventory(*findings, status="established", revision=1):
    """An established inventory. Each finding is ``(id, category)``."""
    return {
        "rubric_version": 1,
        "revision": revision,
        "status": status,
        "findings": [
            {
                "id": fid,
                "description": f"finding {fid}",
                "claim_indices": [position],
                "importance": {
                    "category": category,
                    "weight": {"primary": 2, "supporting": 1, "technical": 0}[category],
                    "rationale": f"why {fid}",
                    "quote": f"quote {fid}",
                    "status": "validated",
                },
            }
            for position, (fid, category) in enumerate(findings)
        ],
    }


def _outcomes(**statuses):
    return {fid: {"status": status, "reason": f"because {fid}"} for fid, status in statuses.items()}


def _card(findings, statuses, **kwargs):
    return build_scorecard(_inventory(*findings), _outcomes(**statuses), **kwargs)


class TestTheWeightsAreTheRubrics:
    def test_primary_is_two_supporting_one_technical_zero(self):
        assert weight_for("primary") == 2
        assert weight_for("supporting") == 1
        assert weight_for("technical") == 0

    @pytest.mark.parametrize("category", ["major", "", None, "3", "critical"])
    def test_an_unknown_category_has_no_weight(self, category):
        with pytest.raises(ValueError):
            weight_for(category)


class TestThePlansExamples:
    """plan_8 section 1, the examples table, row by row."""

    def test_four_supporting_supported_and_one_primary_discrepant(self):
        card = _card(
            [("F1", "supporting"), ("F2", "supporting"), ("F3", "supporting"), ("F4", "supporting"), ("F5", "primary")],
            dict(F1=SUPPORTED, F2=SUPPORTED, F3=SUPPORTED, F4=SUPPORTED, F5=DISCREPANCY),
        )
        assert card["display_score"] == 67
        assert card["score_label"] == "67 / 100"
        assert (card["assessed_count"], card["total_count"]) == (5, 5)
        assert card["scope_label"] == "5 / 5 assessed"
        assert (card["supported_weight"], card["discrepant_weight"], card["assessed_weight"]) == (4, 2, 6)
        assert card["score"] == pytest.approx(100 * 4 / 6)

    def test_one_primary_supported_and_two_supporting_discrepant(self):
        card = _card(
            [("F1", "primary"), ("F2", "supporting"), ("F3", "supporting")],
            dict(F1=SUPPORTED, F2=DISCREPANCY, F3=DISCREPANCY),
        )
        assert card["display_score"] == 50
        assert card["scope_label"] == "3 / 3 assessed"

    def test_four_supporting_supported_and_one_primary_blocked(self):
        card = _card(
            [("F1", "supporting"), ("F2", "supporting"), ("F3", "supporting"), ("F4", "supporting"), ("F5", "primary")],
            dict(F1=SUPPORTED, F2=SUPPORTED, F3=SUPPORTED, F4=SUPPORTED, F5=BLOCKED),
        )
        assert card["display_score"] == 100
        assert card["scope_label"] == "4 / 5 assessed"
        # Access does not subtract points; the blocked primary finding is stated, never hidden.
        assert card["primary_unassessed_count"] == 1
        assert {"kind": "primary_unassessed", "text": "Primary finding remains unassessed", "findings": ["F5"]} in card[
            "messages"
        ]

    def test_six_supported_two_discrepant_two_unassessed(self):
        findings = [(f"F{i}", "supporting") for i in range(1, 11)]
        statuses = {f"F{i}": SUPPORTED for i in range(1, 7)}
        statuses.update(F7=DISCREPANCY, F8=DISCREPANCY, F9=NOT_ATTEMPTED, F10=INCONCLUSIVE)
        card = _card(findings, statuses)
        assert card["display_score"] == 75
        assert card["scope_label"] == "8 / 10 assessed"

    def test_two_supported_out_of_fourteen(self):
        findings = [(f"F{i}", "supporting") for i in range(1, 15)]
        statuses = {f"F{i}": NOT_ATTEMPTED for i in range(1, 15)}
        statuses.update(F1=SUPPORTED, F2=SUPPORTED)
        card = _card(findings, statuses)
        assert card["display_score"] == 100
        assert card["scope_label"] == "2 / 14 assessed"

    def test_four_discrepant_and_none_supported_is_zero(self):
        card = _card(
            [("F1", "primary"), ("F2", "supporting"), ("F3", "supporting"), ("F4", "primary")],
            dict(F1=DISCREPANCY, F2=DISCREPANCY, F3=DISCREPANCY, F4=DISCREPANCY),
        )
        assert card["display_score"] == 0
        assert card["score"] == 0
        assert card["score_label"] == "0 / 100"
        assert card["scope_label"] == "4 / 4 assessed"

    def test_seven_findings_none_conclusively_assessed_has_no_score(self):
        findings = [(f"F{i}", "supporting") for i in range(1, 8)]
        card = _card(findings, {f"F{i}": BLOCKED for i in range(1, 8)})
        assert card["score"] is None
        assert card["display_score"] is None
        assert card["score_label"] is None
        assert card["scope_label"] == "0 / 7 assessed"
        assert card["status"] == "not_assessed"


class TestDisplayRounding:
    def test_nearest_integer_rounds_a_half_up(self):
        assert display_score(supported_weight=1, assessed_weight=8, discrepant_count=1, supported_count=1) == 13

    def test_a_discrepancy_never_displays_one_hundred(self):
        # 199 / 200 is 99.5, which rounds to 100; a discrepancy caps it at 99.
        assert display_score(supported_weight=199, assessed_weight=200, discrepant_count=1, supported_count=150) == 99

    def test_support_never_displays_zero(self):
        # Zero means nothing assessed supported the findings.
        assert display_score(supported_weight=1, assessed_weight=601, discrepant_count=300, supported_count=1) == 1

    def test_no_assessed_weight_has_no_display_score(self):
        assert display_score(supported_weight=0, assessed_weight=0, discrepant_count=0, supported_count=0) is None

    def test_the_exact_score_stays_authoritative_beside_the_display(self):
        findings = [("F1", "primary")] + [(f"F{i}", "supporting") for i in range(2, 201)]
        statuses = {f"F{i}": SUPPORTED for i in range(2, 201)}
        statuses["F1"] = DISCREPANCY
        card = _card(findings, statuses)
        assert card["score"] == pytest.approx(100 * 199 / 201)
        assert card["display_score"] == 99


class TestTheStatesThatAreNotAScore:
    def test_an_unknown_inventory_is_unavailable_and_carries_no_numbers(self):
        card = build_scorecard(None, {})
        assert card["status"] == "unavailable"
        assert card["status_label"] == "Score unavailable for this historical report."
        for key in ("score", "display_score", "total_count", "assessed_count", "supported_count", "assessed_weight"):
            assert card[key] is None, key
        assert card["scope_label"] is None

    def test_an_unestablished_inventory_has_a_null_total_and_says_so(self):
        inventory = _inventory(("F1", "primary"), status="unresolved")
        inventory["reason"] = "the importance of F1 is not established: its quote is not in the paper"
        card = build_scorecard(inventory, _outcomes(F1=SUPPORTED))
        assert card["status"] == "not_established"
        assert card["status_label"] == "Scope not established"
        assert card["total_count"] is None
        assert card["score"] is None
        assert card["reason"] == inventory["reason"]

    def test_an_unknown_inventory_is_distinguishable_from_a_reviewed_empty_one(self):
        empty = build_scorecard(_inventory(("T1", "technical")), _outcomes(T1=SUPPORTED))
        unknown = build_scorecard(None, {})
        assert empty["status"] == "not_applicable"
        assert empty["status_label"] == "Not applicable"
        assert empty["total_count"] == 0
        assert empty["scope_label"] is None  # never "0 / 0" as a performance measure
        assert empty["reason"]
        assert unknown["status"] == "unavailable"
        assert unknown["total_count"] is None

    def test_an_active_study_is_marked_in_progress(self):
        card = _card([("F1", "primary")], dict(F1=SUPPORTED), in_progress=True)
        assert card["in_progress"] is True
        assert card["in_progress_label"] == "In progress"


class TestIntegrity:
    def test_every_scoreable_finding_is_in_exactly_one_list(self):
        card = _card(
            [("F1", "primary"), ("F2", "supporting"), ("F3", "supporting"), ("F4", "primary")],
            dict(F1=SUPPORTED, F2=DISCREPANCY, F3=UNRESOLVED, F4=NOT_ATTEMPTED),
        )
        assessed = [i["finding_id"] for i in card["assessed_items"]]
        unassessed = [i["finding_id"] for i in card["unassessed_items"]]
        assert sorted(assessed + unassessed) == ["F1", "F2", "F3", "F4"]
        assert len(assessed) == card["assessed_count"]
        assert len(assessed) + len(unassessed) == card["total_count"]
        assert card["assessed_count"] == card["supported_count"] + card["discrepant_count"]

    def test_weight_zero_checks_change_neither_metric_whether_they_pass_or_fail(self):
        base = _card([("F1", "primary"), ("F2", "supporting")], dict(F1=SUPPORTED, F2=DISCREPANCY))
        for technical in (SUPPORTED, DISCREPANCY, BLOCKED):
            with_check = _card(
                [("F1", "primary"), ("F2", "supporting"), ("T1", "technical")],
                dict(F1=SUPPORTED, F2=DISCREPANCY, T1=technical),
            )
            for key in ("score", "display_score", "assessed_count", "total_count", "assessed_weight"):
                assert with_check[key] == base[key], (technical, key)
            assert [i["finding_id"] for i in with_check["excluded_items"]] == ["T1"]

    def test_a_finding_with_no_outcome_is_not_attempted_and_stays_in_the_total(self):
        card = build_scorecard(_inventory(("F1", "primary"), ("F2", "supporting")), _outcomes(F1=SUPPORTED))
        assert card["total_count"] == 2
        [missing] = card["unassessed_items"]
        assert missing["finding_id"] == "F2"
        assert missing["status"] == NOT_ATTEMPTED

    def test_inconclusive_and_blocked_findings_remain_in_the_total(self):
        card = _card(
            [("F1", "supporting"), ("F2", "supporting"), ("F3", "supporting")],
            dict(F1=SUPPORTED, F2=INCONCLUSIVE, F3=BLOCKED),
        )
        assert (card["assessed_count"], card["total_count"]) == (1, 3)

    def test_a_stored_weight_that_is_not_the_rubrics_is_refused(self):
        inventory = _inventory(("F1", "primary"))
        inventory["findings"][0]["importance"]["weight"] = 3
        with pytest.raises(ScorecardInvariantError):
            build_scorecard(inventory, _outcomes(F1=SUPPORTED))

    def test_an_unknown_outcome_status_is_refused(self):
        with pytest.raises(ScorecardInvariantError):
            build_scorecard(_inventory(("F1", "primary")), {"F1": {"status": "partially_supported"}})

    def test_a_duplicate_finding_id_is_refused(self):
        inventory = _inventory(("F1", "primary"), ("F1", "supporting"))
        with pytest.raises(ScorecardInvariantError):
            build_scorecard(inventory, _outcomes(F1=SUPPORTED))


class TestPresentation:
    def test_the_explanation_and_the_rubric_are_named(self):
        card = _card([("F1", "primary")], dict(F1=SUPPORTED))
        assert card["title"] == "Validation Scorecard"
        assert card["rubric_version"] == 1
        assert card["rubric_label"] == "weighted rubric version 1"
        assert card["explanation"] == (
            "The score measures agreement among assessed findings, with primary findings weighted twice as much "
            "as supporting findings. Scope counts assessed findings out of the total."
        )

    def test_the_summary_sentence_names_categories_and_outcomes(self):
        card = _card(
            [("F1", "supporting"), ("F2", "supporting"), ("F3", "supporting"), ("F4", "supporting"), ("F5", "primary")],
            dict(F1=SUPPORTED, F2=SUPPORTED, F3=SUPPORTED, F4=SUPPORTED, F5=DISCREPANCY),
        )
        assert card["summary"] == "Four supporting findings supported; one primary finding discrepant."

    def test_the_summary_sentence_counts_what_was_not_assessed(self):
        findings = [(f"F{i}", "supporting") for i in range(1, 15)]
        statuses = {f"F{i}": NOT_ATTEMPTED for i in range(1, 15)}
        statuses.update(F1=SUPPORTED, F2=SUPPORTED)
        assert (
            _card(findings, statuses)["summary"]
            == "Two supporting findings supported; 12 supporting findings not assessed."
        )

    def test_a_primary_discrepancy_is_stated_below_the_metrics(self):
        card = _card([("F1", "primary"), ("F2", "supporting")], dict(F1=DISCREPANCY, F2=SUPPORTED))
        assert card["primary_discrepancy_count"] == 1
        assert {"kind": "primary_discrepancy", "text": "Primary discrepancy: finding F1", "findings": ["F1"]} in card[
            "messages"
        ]

    def test_several_unassessed_primary_findings_are_pluralized(self):
        card = _card([("F1", "primary"), ("F2", "primary")], dict(F1=BLOCKED, F2=INCONCLUSIVE))
        [message] = [m for m in card["messages"] if m["kind"] == "primary_unassessed"]
        assert message["text"] == "Primary findings remain unassessed"
        assert message["findings"] == ["F1", "F2"]

    def test_primary_discrepancies_come_first_among_the_assessed(self):
        card = _card(
            [("F1", "supporting"), ("F2", "primary"), ("F3", "supporting"), ("F4", "primary")],
            dict(F1=SUPPORTED, F2=SUPPORTED, F3=DISCREPANCY, F4=DISCREPANCY),
        )
        assert [i["finding_id"] for i in card["assessed_items"]] == ["F4", "F1", "F2", "F3"]

    def test_primary_findings_come_first_among_the_unassessed(self):
        card = _card(
            [("F1", "supporting"), ("F2", "primary"), ("F3", "supporting")],
            dict(F1=BLOCKED, F2=NOT_ATTEMPTED, F3=INCONCLUSIVE),
        )
        assert [i["finding_id"] for i in card["unassessed_items"]] == ["F2", "F1", "F3"]

    def test_every_item_states_its_category_weight_rationale_and_status(self):
        card = _card([("F1", "primary"), ("F2", "supporting")], dict(F1=SUPPORTED, F2=INCONCLUSIVE))
        [assessed] = card["assessed_items"]
        assert assessed["category_label"] == "Primary"
        assert assessed["weight"] == 2
        assert assessed["rationale"] == "why F1"
        assert assessed["status_label"] == "Supported"
        [unassessed] = card["unassessed_items"]
        assert unassessed["category_label"] == "Supporting"
        assert unassessed["status_label"] == "Attempted; result inconclusive"
        assert unassessed["reason"] == "because F2"

    def test_each_unassessed_state_reads_differently(self):
        labels = {
            status: _card([("F1", "supporting")], {"F1": status})["unassessed_items"][0]["status_label"]
            for status in (BLOCKED, UNRESOLVED, INCONCLUSIVE, NOT_ATTEMPTED)
        }
        assert len(set(labels.values())) == 4


class TestTheCompactForm:
    def test_the_list_carries_the_two_metrics_and_the_primary_indicators(self):
        card = _card(
            [("F1", "supporting"), ("F2", "supporting"), ("F3", "supporting"), ("F4", "supporting"), ("F5", "primary")],
            dict(F1=SUPPORTED, F2=SUPPORTED, F3=SUPPORTED, F4=SUPPORTED, F5=DISCREPANCY),
        )
        compact = compact_scorecard(card)
        assert compact == {
            "status": "scored",
            "status_label": None,
            "score": card["score"],
            "display_score": 67,
            "score_label": "67 / 100",
            "assessed_count": 5,
            "total_count": 5,
            "scope_label": "5 / 5 assessed",
            "primary_discrepancy_count": 1,
            "primary_unassessed_count": 0,
            "in_progress": False,
            "rubric_version": 1,
            "inventory_revision": 1,
        }

    def test_the_compact_form_of_an_unassessed_study_keeps_its_scope(self):
        findings = [(f"F{i}", "supporting") for i in range(1, 8)]
        compact = compact_scorecard(_card(findings, {f"F{i}": BLOCKED for i in range(1, 8)}))
        assert compact["score_label"] is None
        assert compact["scope_label"] == "0 / 7 assessed"
