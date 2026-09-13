"""plan_8_1 section 2.3: a finding's membership and its importance are established separately.

Under plan_8, one weak finding voided the whole inventory: a quote the text search could not find left
"Scope not established" for every finding. Importance uncertainty is not membership uncertainty:

- **Membership** is established when a finding holds at least one claim and none of its claims is in
  another finding; the inventory's, when every committed claim is in exactly one finding.
- **Importance** is ``validated`` (plan_8's rules), ``proposed`` (a valid category whose support failed)
  or ``unknown`` (no valid category).

N counts every validated primary or supporting finding plus every finding whose importance is not
validated, whatever category was proposed; only a finding validated as technical leaves N. An assessed
finding weighs only when its importance is validated. Importance uncertainty alone never blanks the
scope; unresolved membership can.
"""

import pytest
import pytest_asyncio

from app.services.validation_finding_inventory import inventory_from_proposal
from app.services.validation_scorecard import (
    DISCREPANCY,
    NOT_ATTEMPTED,
    SUPPORTED,
    build_scorecard,
    compact_scorecard,
)

_TEXT = "The knockout changes hundreds of genes. This is our main result. A smaller shift extends it."
_TARGETS = [{"claim_text": f"claim {i}"} for i in range(4)]
_MODEL = {"kind": "model", "model": "m"}


def _finding(indices, importance="supporting", quote="A smaller shift extends it.", rationale="It extends it."):
    return {
        "description": f"finding over {indices}",
        "claim_indices": indices,
        "importance": importance,
        "rationale": rationale,
        "quote": quote,
    }


def _valid():
    return [
        _finding([0, 1], "primary", "This is our main result."),
        _finding([2]),
        _finding([3], "technical", None, "Depth enables it."),
    ]


def _inventory(proposal):
    return inventory_from_proposal(proposal, targets=_TARGETS, full_text=_TEXT, decided_by=_MODEL)


class TestTwoStatusesPerFinding:
    def test_a_valid_proposal_establishes_membership_and_importance(self):
        inventory = _inventory(_valid())
        assert inventory["status"] == "established"
        assert inventory["membership"]["status"] == "established"
        assert all(f["membership"]["status"] == "established" for f in inventory["findings"])
        assert all(f["importance"]["status"] == "validated" for f in inventory["findings"])

    def test_a_quote_not_found_is_a_proposed_importance_and_a_provisional_scope(self):
        proposal = _valid()
        proposal[1]["quote"] = "words the paper never wrote"
        inventory = _inventory(proposal)
        f2 = inventory["findings"][1]
        assert (f2["importance"]["status"], f2["importance"]["category"]) == ("proposed", "supporting")
        assert "quote" in f2["importance"]["problem"]
        assert f2["membership"]["status"] == "established"
        assert inventory["status"] == "provisional"

    def test_no_valid_category_is_an_unknown_importance(self):
        proposal = _valid()
        proposal[1]["importance"] = "major"
        inventory = _inventory(proposal)
        assert inventory["findings"][1]["importance"]["status"] == "unknown"
        assert inventory["status"] == "provisional"

    def test_a_claim_in_no_finding_leaves_membership_unestablished(self):
        inventory = _inventory(_valid()[:2])
        assert inventory["membership"]["status"] == "not_established"
        assert inventory["status"] == "unresolved"
        assert inventory["unplaced_claims"] == [3]

    def test_a_claim_in_two_findings_leaves_that_finding_unestablished(self):
        proposal = _valid()
        proposal[1]["claim_indices"] = [1, 2]
        inventory = _inventory(proposal)
        assert inventory["findings"][1]["membership"]["status"] == "not_established"
        assert inventory["status"] == "unresolved"

    def test_a_finding_holding_no_claim_is_not_established(self):
        proposal = _valid() + [_finding([])]
        inventory = _inventory(proposal)
        assert inventory["findings"][3]["membership"]["status"] == "not_established"
        assert inventory["status"] == "unresolved"


def _card(proposal, outcomes):
    return build_scorecard(_inventory(proposal), {k: {"status": v} for k, v in outcomes.items()})


class TestTheDenominator:
    def test_only_a_finding_validated_as_technical_leaves_n(self):
        card = _card(_valid(), {"F1": SUPPORTED, "F2": SUPPORTED})
        assert card["total_count"] == 2
        assert [i["finding_id"] for i in card["excluded_items"]] == ["F3"]

    def test_a_finding_proposed_as_technical_but_not_validated_stays_in_n(self):
        proposal = _valid()
        proposal[2]["rationale"] = ""
        card = _card(proposal, {"F1": SUPPORTED, "F2": SUPPORTED})
        assert card["total_count"] == 3
        assert card["excluded_items"] == []
        assert card["provisional"] is True

    def test_an_unassessed_finding_of_unestablished_importance_leaves_the_score_and_marks_the_scope(self):
        proposal = _valid()
        proposal[1]["quote"] = "nowhere"
        card = _card(proposal, {"F1": SUPPORTED, "F2": NOT_ATTEMPTED})
        assert card["score_label"] == "100 / 100"
        assert card["scope_label"] == "1 / 2 assessed (provisional)"
        assert card["provisional"] is True
        assert "falls by one" in card["provisional_note"]

    def test_an_assessed_finding_of_unestablished_importance_withholds_the_score(self):
        proposal = _valid()
        proposal[1]["quote"] = "nowhere"
        card = _card(proposal, {"F1": SUPPORTED, "F2": DISCREPANCY})
        assert card["display_score"] is None
        assert card["score_label"] is None
        assert card["score_status_label"] == "Score pending importance review"
        assert card["scope_label"] == "2 / 2 assessed (provisional)"

    def test_an_unestablished_finding_is_listed_with_its_problem_and_never_weighted(self):
        proposal = _valid()
        proposal[1]["importance"] = "major"
        card = _card(proposal, {"F1": SUPPORTED})
        item = next(i for i in card["unassessed_items"] if i["finding_id"] == "F2")
        assert item["weight"] is None
        assert item["importance_status"] == "unknown"
        assert "major" in item["importance_problem"]

    def test_the_primary_warning_covers_findings_of_unestablished_importance(self):
        proposal = _valid()
        proposal[1]["quote"] = "nowhere"
        card = _card(proposal, {"F1": SUPPORTED})
        texts = [m["text"] for m in card["messages"]]
        assert "1 unassessed finding has no established importance; it could be primary." in texts
        assert any(i["kind"] == "importance_unestablished" for i in card["indicators"])

    def test_unresolved_membership_gives_scope_not_established(self):
        card = _card(_valid()[:2], {"F1": SUPPORTED})
        assert card["status"] == "not_established"
        assert card["scope_label"] is None

    def test_the_list_carries_provisional_in_the_scope(self):
        proposal = _valid()
        proposal[1]["quote"] = "nowhere"
        compact = compact_scorecard(_card(proposal, {"F1": SUPPORTED}))
        assert compact["scope_label"] == "1 / 2 assessed (provisional)"
        assert compact["provisional"] is True


class TestProvisionalReadsTheSameOnEverySurface:
    """The report, the list, the JSON export and the markdown, through their entry points."""

    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    @pytest.mark.asyncio
    async def test_provisional_on_report_list_json_and_markdown(self, client, session, admin_user, admin_token):
        from app.services.reproduction_plan_service import ReproductionPlanService
        from tests.test_scorecard_surfaces import TestEverySurfaceShowsTheSameScorecard, _seed

        study = await _seed(session, admin_user)
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        inventory = dict(plan.finding_inventory_json)
        findings = [dict(f) for f in inventory["findings"]]
        # The first (supported) finding's quote was not found: its importance is proposed, not validated.
        findings[0] = {
            **findings[0],
            "importance": {
                **findings[0]["importance"],
                "status": "proposed",
                "problem": "its quote is not in the paper's text",
            },
        }
        inventory.update(findings=findings, status="provisional")
        plan.finding_inventory_json = inventory
        await session.commit()

        report, row, exported, text = await TestEverySurfaceShowsTheSameScorecard()._surfaces(
            client, session, admin_user, admin_token, study
        )
        assert report["scope_label"].endswith("(provisional)")
        assert row["scope_label"] == exported["scope_label"] == report["scope_label"]
        assert row["provisional"] is exported["provisional"] is True
        # The assessed finding of unestablished importance withholds the score everywhere.
        assert report["score_label"] is None and row["score_label"] is None
        assert report["score_status_label"] == row["score_status_label"] == "Score pending importance review"
        section = text.split("## Validation Scorecard", 1)[1].split("\n## ", 1)[0]
        assert report["scope_label"] in section
        assert "Score pending importance review" in section
        assert "Importance not established: its quote is not in the paper's text" in section
