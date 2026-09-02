"""plan_6 step 5: the autonomy setting, and the decision list the C1 gate renders.

One org-scoped setting with two values. `assisted` (the default) surfaces anything the model
declined for a person to resolve at the C1 gate; `autonomous` requires the model to choose and
records how sure it was. The C1 gate itself stays human in BOTH modes: it authorises the spend.
"""

import pytest
import pytest_asyncio

from app.services.validation_autonomy import (
    AUTONOMY_ASSISTED,
    AUTONOMY_AUTONOMOUS,
    VALID_AUTONOMY,
    decision_list,
)


class TestTheSetting:
    @pytest.mark.asyncio
    async def test_it_defaults_to_assisted(self, client, admin_token):
        r = await client.get(
            "/api/literature/settings/lit-validation", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert r.status_code == 200
        assert r.json()["autonomy"] == AUTONOMY_ASSISTED

    @pytest.mark.asyncio
    async def test_it_round_trips(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        put = await client.put(
            "/api/literature/settings/lit-validation", json={"autonomy": AUTONOMY_AUTONOMOUS}, headers=headers
        )
        assert put.status_code == 200, put.text
        assert put.json()["autonomy"] == AUTONOMY_AUTONOMOUS

        got = await client.get("/api/literature/settings/lit-validation", headers=headers)
        assert got.json()["autonomy"] == AUTONOMY_AUTONOMOUS

    @pytest.mark.asyncio
    async def test_it_refuses_a_mode_that_does_not_exist(self, client, admin_token):
        r = await client.put(
            "/api/literature/settings/lit-validation",
            json={"autonomy": "full_self_driving"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 400
        assert "assisted" in r.text and "autonomous" in r.text

    @pytest.mark.asyncio
    async def test_a_viewer_cannot_change_it(self, client, viewer_token):
        r = await client.put(
            "/api/literature/settings/lit-validation",
            json={"autonomy": AUTONOMY_AUTONOMOUS},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert r.status_code == 403

    def test_the_two_modes_are_the_whole_vocabulary(self):
        assert VALID_AUTONOMY == (AUTONOMY_ASSISTED, AUTONOMY_AUTONOMOUS)


def _target(metric_key, bound_key=None, reason=None, confidence=None, model=None, bound_by="model"):
    return {
        "metric_key": metric_key,
        "bound_key": bound_key,
        "binding_reason": reason,
        "binding_confidence": confidence,
        "bound_by_model": model,
        "bound_by": bound_by,
    }


class TestTheDecisionList:
    """The C1 gate renders what the model decided in BOTH modes. A decision that cannot be
    attributed is a defect, so every row carries the model that made it and how sure it was."""

    def test_every_row_names_the_model_and_its_confidence(self):
        rows = decision_list(
            [
                _target("samd1_chip_peaks", "peak_count", "the paper's headline peak number", 0.94, "claude-opus-4-8"),
                _target("deg_count", None, "a DE gene count is not a controlled metric", 0.97, "claude-opus-4-8"),
            ]
        )
        assert [r["resolved"] for r in rows] == [True, False]
        assert all(r["model"] == "claude-opus-4-8" for r in rows)
        assert all(r["confidence"] is not None for r in rows)
        assert rows[0]["bound_key"] == "peak_count"
        assert rows[0]["reason"]

    def test_a_low_confidence_row_is_marked(self):
        """The gate shows 0.61 differently from 0.94, because that is the row a person should read."""
        rows = decision_list([_target("reference_build", "peak_count", "reading intent from context", 0.61, "m")])
        assert rows[0]["low_confidence"] is True

    def test_a_confident_row_is_not_marked(self):
        rows = decision_list([_target("peaks", "peak_count", "stated in so many words", 0.94, "m")])
        assert rows[0]["low_confidence"] is False

    def test_a_row_the_alias_table_decided_says_so(self):
        """Not every binding is the model's. A plan whose binding call failed fell back, and the gate
        must not present that as an AI decision."""
        rows = decision_list([_target("alignment_rate", None, None, None, None, bound_by="alias_table")])
        assert rows[0]["model"] is None
        assert rows[0]["decided_by"] == "alias_table"
        assert rows[0]["low_confidence"] is False

    def test_it_counts_what_was_resolved(self):
        rows = decision_list(
            [
                _target("a", "peak_count", "r", 0.9, "m"),
                _target("b", "frip", "r", 0.9, "m"),
                _target("c", None, "r", 0.9, "m"),
            ]
        )
        assert sum(1 for r in rows if r["resolved"]) == 2
        assert len(rows) == 3

    def test_no_targets_is_an_empty_list_not_an_error(self):
        assert decision_list([]) == []


@pytest_asyncio.fixture
async def _enable_lit_validation(session):
    """lit_validation is gated behind its beta flag; without it every endpoint 404s."""
    from app.services import beta_features_service

    await beta_features_service.set_flag(session, "lit_validation", True)
    await session.commit()


class TestTheGateSeesTheDecisions:
    """The plan the C1 gate reads carries the AI decision list, in both modes. Without it the gate
    shows a bound metric key and no way to tell whether a model chose it or a lookup table did."""

    @pytest.mark.asyncio
    async def test_the_plan_response_carries_the_decision_list(
        self, client, admin_token, session, admin_user, _enable_lit_validation
    ):
        from app.services.reproduction_plan_service import ReproductionPlanService
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        await session.flush()
        plan = await ReproductionPlanService.create_plan(
            session, study, admin_user.id, accessions=["GSE1"], pipeline_key="nf-core/chipseq"
        )
        await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {
                    "metric_key": "samd1_chip_peaks",
                    "claimed_value": 8733.0,
                    "bound_key": "peak_count",
                    "binding_reason": "the paper's headline peak number",
                    "binding_confidence": 0.94,
                    "bound_by_model": "claude-opus-4-8",
                    "bound_by": "model",
                }
            ],
        )
        await session.commit()

        r = await client.get(f"/api/validation-studies/{study.id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, r.text
        decisions = r.json()["plan"]["ai_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["bound_key"] == "peak_count"
        assert decisions[0]["model"] == "claude-opus-4-8"
        assert decisions[0]["confidence"] == 0.94
        assert decisions[0]["low_confidence"] is False
        assert decisions[0]["reason"] == "the paper's headline peak number"
