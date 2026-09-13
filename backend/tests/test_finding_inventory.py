"""plan_8 section 2: a stable, reviewed inventory of the paper's distinct computational findings.

The denominator is findings, not files, outputs, extracted numbers or successful comparisons. A model
may propose the grouping and each finding's importance category; it cannot choose a weight, and a
proposal the fixed rubric cannot validate leaves the inventory unestablished rather than defaulted.
"""

import pytest

from app.services.validation_finding_inventory import (
    ESTABLISHED,
    NOT_APPLICABLE,
    PROVISIONAL,
    UNRESOLVED,
    inventory_from_proposal,
    revise_importance,
)

_TEXT = (
    "Loss of the regulator changes the expression of hundreds of genes in stem cells. "
    "This is the main result of our study. "
    "The differentiated cells show a smaller shift that extends the main result. "
    "Libraries were sequenced to a depth sufficient for differential analysis."
)

_TARGETS = [
    {
        "claim_text": "hundreds of genes up",
        "source_locator": "Fig. 2A",
        "reported_experiment_id": "e2",
        "contrast_index": 0,
    },
    {
        "claim_text": "hundreds of genes down",
        "source_locator": "Fig. 2A",
        "reported_experiment_id": "e2",
        "contrast_index": 0,
    },
    {
        "claim_text": "a smaller shift on day 7",
        "source_locator": "Fig. 3",
        "reported_experiment_id": "e2",
        "contrast_index": 1,
    },
    {"claim_text": "reads per sample", "source_locator": "Methods", "reported_experiment_id": "e2"},
]

_MODEL = {"kind": "model", "model": "a-model"}


def _proposal():
    return [
        {
            "description": "The regulator changes the expression of hundreds of genes",
            "claim_indices": [0, 1],
            "importance": "primary",
            "rationale": "The paper's main conclusion rests on it.",
            "quote": "This is the main result of our study.",
        },
        {
            "description": "A smaller shift in differentiated cells",
            "claim_indices": [2],
            "importance": "supporting",
            "rationale": "It extends the main result to a second state.",
            "quote": "a smaller shift that extends the main result",
        },
        {
            "description": "Sequencing depth",
            "claim_indices": [3],
            "importance": "technical",
            "rationale": "It enables the differential analysis and establishes no finding.",
            "prerequisite_for": [0, 1],
        },
    ]


def _inventory(proposal=None, **kwargs):
    return inventory_from_proposal(
        _proposal() if proposal is None else proposal,
        targets=kwargs.pop("targets", _TARGETS),
        full_text=kwargs.pop("full_text", _TEXT),
        decided_by=_MODEL,
        **kwargs,
    )


class TestAValidProposalIsEstablished:
    def test_it_is_established_at_revision_one_under_rubric_one(self):
        inventory = _inventory()
        assert inventory["status"] == ESTABLISHED
        assert inventory["revision"] == 1
        assert inventory["rubric_version"] == 1
        assert inventory["reason"] is None

    def test_each_finding_has_a_stable_id_and_its_claims(self):
        findings = _inventory()["findings"]
        assert [f["id"] for f in findings] == ["F1", "F2", "F3"]
        assert [f["claim_indices"] for f in findings] == [[0, 1], [2], [3]]

    def test_the_weight_comes_from_the_category_and_the_rationale_is_kept(self):
        primary, supporting, technical = _inventory()["findings"]
        assert (primary["importance"]["category"], primary["importance"]["weight"]) == ("primary", 2)
        assert (supporting["importance"]["category"], supporting["importance"]["weight"]) == ("supporting", 1)
        assert (technical["importance"]["category"], technical["importance"]["weight"]) == ("technical", 0)
        assert primary["importance"]["rationale"] == "The paper's main conclusion rests on it."
        assert primary["importance"]["quote"] == "This is the main result of our study."

    def test_who_proposed_and_who_validated_are_recorded(self):
        importance = _inventory()["findings"][0]["importance"]
        assert importance["status"] == "validated"
        assert importance["decided_by"] == "model"
        assert importance["model"] == "a-model"
        assert importance["validated_by"] == "weighted rubric version 1"

    def test_a_finding_carries_its_locator_experiment_and_contrast(self):
        primary, supporting, _ = _inventory()["findings"]
        assert primary["locator"] == "Fig. 2A"
        assert primary["experiment_id"] == "e2"
        assert primary["contrast_index"] == 0
        assert supporting["contrast_index"] == 1

    def test_every_claim_of_a_finding_is_a_required_subcheck_under_fixed_criteria(self):
        primary = _inventory()["findings"][0]
        assert primary["required"] == [0, 1]
        assert primary["criteria"]["rule"] == "all_required_subchecks"
        assert "every required claim" in primary["criteria"]["words"]

    def test_a_technical_check_names_the_findings_it_is_a_prerequisite_for(self):
        technical = _inventory()["findings"][2]
        assert technical["prerequisite_for"] == ["F1", "F2"]

    def test_the_rubrics_full_category_names_are_read_too(self):
        proposal = _proposal()
        proposal[0]["importance"] = "Primary finding"
        proposal[2]["importance"] = "technical prerequisite or descriptive check"
        findings = _inventory(proposal)["findings"]
        assert findings[0]["importance"]["category"] == "primary"
        assert findings[2]["importance"]["category"] == "technical"

    def test_claim_positions_are_translated_to_the_claims_that_were_kept(self):
        # The model indexes the claims it read; a claim with no text was never kept as a target.
        proposal = [dict(f, claim_indices=[i + 1 for i in f["claim_indices"]]) for f in _proposal()]
        inventory = _inventory(proposal, claim_targets={1: 0, 2: 1, 3: 2, 4: 3})
        assert [f["claim_indices"] for f in inventory["findings"]] == [[0, 1], [2], [3]]


class TestAProposalTheRubricCannotValidate:
    def _problem(self, inventory, finding_id):
        finding = next(f for f in inventory["findings"] if f["id"] == finding_id)
        return finding["importance"]["status"], finding["importance"]["problem"]

    def test_an_arbitrary_weight_is_rejected(self):
        proposal = _proposal()
        proposal[1]["weight"] = 3
        inventory = _inventory(proposal)
        # plan_8_1 section 2.3: an importance the rubric cannot validate is proposed, and the scope is
        # provisional rather than unestablished.
        assert inventory["status"] == PROVISIONAL
        status, problem = self._problem(inventory, "F2")
        assert status == "proposed"
        assert "weight" in problem
        # The finding keeps the rubric's weight for its category, never the model's number.
        assert inventory["findings"][1]["importance"]["weight"] == 1

    def test_a_weight_equal_to_the_rubrics_is_harmless(self):
        proposal = _proposal()
        proposal[0]["weight"] = 2
        assert _inventory(proposal)["status"] == ESTABLISHED

    def test_an_unknown_category_is_unresolved(self):
        proposal = _proposal()
        proposal[1]["importance"] = "major"
        inventory = _inventory(proposal)
        assert inventory["status"] == PROVISIONAL
        status, problem = self._problem(inventory, "F2")
        assert status == "unknown"
        assert "major" in problem
        assert inventory["findings"][1]["importance"]["weight"] is None

    def test_a_quote_the_paper_does_not_contain_is_unresolved(self):
        proposal = _proposal()
        proposal[0]["quote"] = "a sentence the paper never wrote"
        inventory = _inventory(proposal)
        assert inventory["status"] == PROVISIONAL
        assert "quote" in self._problem(inventory, "F1")[1]

    def test_a_scoreable_finding_without_a_quote_is_unresolved(self):
        proposal = _proposal()
        del proposal[1]["quote"]
        assert _inventory(proposal)["status"] == PROVISIONAL

    def test_a_finding_without_a_rationale_is_unresolved(self):
        proposal = _proposal()
        proposal[2]["rationale"] = "  "
        assert _inventory(proposal)["status"] == PROVISIONAL

    def test_a_claim_in_no_finding_is_never_silently_omitted(self):
        proposal = _proposal()[:2]
        inventory = _inventory(proposal)
        assert inventory["status"] == UNRESOLVED
        assert inventory["unplaced_claims"] == [3]
        assert "claim 4" in inventory["reason"].lower()

    def test_a_claim_in_two_findings_is_unresolved(self):
        proposal = _proposal()
        proposal[1]["claim_indices"] = [1, 2]
        inventory = _inventory(proposal)
        assert inventory["status"] == UNRESOLVED
        # plan_8_1 section 2.3: where a claim sits is the finding's membership, not its importance.
        assert "claim 2" in "; ".join(inventory["findings"][1]["membership"]["problems"])

    def test_the_reason_names_every_finding_whose_importance_is_open(self):
        proposal = _proposal()
        proposal[0]["quote"] = "nowhere"
        proposal[1]["importance"] = "major"
        reason = _inventory(proposal)["reason"]
        assert "F1" in reason and "F2" in reason

    def test_a_reading_that_proposed_no_findings_for_its_claims_is_unresolved(self):
        inventory = inventory_from_proposal(None, targets=_TARGETS, full_text=_TEXT, decided_by=_MODEL)
        assert inventory["status"] == UNRESOLVED
        assert inventory["findings"] == []
        assert inventory["reason"]

    def test_a_reading_that_could_not_be_parsed_is_unresolved(self):
        inventory = _inventory([], targets=[], parse_failure=True)
        assert inventory["status"] == UNRESOLVED
        assert "could not be read" in inventory["reason"]


class TestNotApplicable:
    def test_a_paper_with_no_claims_and_no_findings_has_nothing_to_score(self):
        inventory = _inventory([], targets=[])
        assert inventory["status"] == NOT_APPLICABLE
        assert inventory["reason"]

    def test_an_inventory_of_technical_checks_only_is_not_applicable(self):
        proposal = [
            {
                "description": "Depth",
                "claim_indices": [0],
                "importance": "technical",
                "rationale": "It enables the analysis.",
            }
        ]
        inventory = _inventory(proposal, targets=_TARGETS[3:])
        assert inventory["status"] == NOT_APPLICABLE
        assert "technical" in inventory["reason"]


class TestAJustifiedCorrectionIsARevision:
    def test_the_correction_is_a_new_revision_and_the_old_one_is_history(self):
        before = _inventory()
        after = revise_importance(
            before,
            "F2",
            category="primary",
            rationale="The discussion treats the differentiated state as a main conclusion.",
            reason="the reading underweighted it",
            decided_by={"kind": "person", "user_id": 7},
            snapshot={"display_score": 67, "scope_label": "1 / 2 assessed"},
        )
        assert after["revision"] == 2
        assert after["findings"][1]["importance"]["category"] == "primary"
        assert after["findings"][1]["importance"]["weight"] == 2
        assert after["findings"][1]["importance"]["decided_by"] == "person"
        assert after["findings"][1]["id"] == "F2"
        [previous] = after["history"]
        assert previous["revision"] == 1
        assert previous["findings"][1]["importance"]["category"] == "supporting"
        assert previous["superseded_reason"] == "the reading underweighted it"
        assert previous["scorecard"] == {"display_score": 67, "scope_label": "1 / 2 assessed"}
        # The revision it replaced is untouched.
        assert before["revision"] == 1
        assert before["findings"][1]["importance"]["category"] == "supporting"

    def test_a_correction_resolves_an_open_importance(self):
        proposal = _proposal()
        proposal[1]["importance"] = "major"
        before = _inventory(proposal)
        after = revise_importance(
            before,
            "F2",
            category="supporting",
            rationale="It extends the main result.",
            reason="resolving the open importance",
            decided_by={"kind": "person", "user_id": 7},
        )
        assert before["status"] == PROVISIONAL
        assert after["status"] == ESTABLISHED
        assert after["reason"] is None

    @pytest.mark.parametrize(
        "change",
        [
            {"category": "critical"},
            {"rationale": ""},
            {"reason": ""},
            {"finding_id": "F9"},
        ],
    )
    def test_an_unjustified_or_unknown_correction_is_refused(self, change):
        arguments = {
            "finding_id": "F2",
            "category": "primary",
            "rationale": "a reason",
            "reason": "a justification",
        }
        arguments.update(change)
        finding_id = arguments.pop("finding_id")
        with pytest.raises(ValueError):
            revise_importance(_inventory(), finding_id, decided_by={"kind": "person", "user_id": 7}, **arguments)
