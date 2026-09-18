"""plan_8_3 section 0.2: the live regression studies replay offline and reproduce what was recorded.

Gate 0. Studies 55 (Groff), 56 (SAMD1) and 57 (the substrate-stiffness paper) are where the deployed
build stops, and every repair in plan_8_3 is verified against them. Their evidence is captured as
committed fixtures, and these tests establish that driving bioAF's own callers over that evidence
reaches the outcomes the live run recorded, with no paper read, no model call and no compute.

A repair that cannot be demonstrated here is not ready for a live run.
"""

import json

import pytest

from tests.replay import SAVED_STUDIES, load, replay_consistency, replay_mapping, replay_report, restore


async def _restored(session, admin_user, study_id: int):
    return await restore(
        session, study_id, organization_id=admin_user.organization_id, user_id=admin_user.id
    )


class TestTheCapturedFixturesAreRedistributable:
    """The fixture rules: no account, environment or credential detail reaches a committed fixture."""

    @pytest.mark.parametrize("study_id", sorted(SAVED_STUDIES))
    def test_no_account_or_environment_detail_is_committed(self, study_id):
        text = SAVED_STUDIES[study_id].read_text()
        for forbidden in (
            "bioaf-495400",
            "bioaf-demo",
            "@bioaf.co",
            "bioaf-results-bioaf-co",
            "sk-ant",
            "/home/brentmills",
        ):
            assert forbidden not in text, f"study {study_id}'s fixture holds {forbidden}"
        bundle = json.loads(text)
        for dropped in ("organization_id", "requested_by_user_id", "approved_by_user_id"):
            assert dropped not in bundle["study"]


class TestTheSavedStudiesRestoreAsTheyWereRecorded:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("study_id", sorted(SAVED_STUDIES))
    async def test_the_study_its_plan_its_targets_and_its_checks_come_back(self, session, admin_user, study_id):
        bundle = load(study_id)
        restored = await _restored(session, admin_user, study_id)
        assert restored.study.id == study_id
        assert restored.study.state == bundle["study"]["state"]
        assert restored.study.classification == bundle["study"]["classification"]
        assert restored.plan.id == bundle["reproduction_plans"][0]["id"]
        assert len(restored.targets) == len(bundle["comparison_targets"])
        assert len(restored.checks) == len(bundle["check_records"])


class TestGroffsParentCountAgreesAndItsRefinementIsNeverReached:
    """Study 55's recorded stop. The 194-gene parent list AGREES; the 88-gene refinement of that same
    list is `not_checkable` because the claim carries no significance cutoff, so section 1.2's subset
    operation is never reached on the paper it was written for."""

    @pytest.mark.asyncio
    async def test_replaying_the_checks_reproduces_both_outcomes(self, session, admin_user):
        restored = await _restored(session, admin_user, 55)
        outcomes = await replay_consistency(session, restored)
        assert outcomes[10]["outcome"] == "agree"
        assert outcomes[10]["rows_passing"] == 194
        assert outcomes[11]["outcome"] == "not_checkable"
        assert outcomes[11]["reason"] == "the claim states no significance cutoff, and bioAF supplies none"

    @pytest.mark.asyncio
    async def test_the_replayed_table_is_the_one_the_live_run_read(self, session, admin_user):
        restored = await _restored(session, admin_user, 55)
        outcomes = await replay_consistency(session, restored)
        recorded = next(
            c["outcome_json"]["decoding"]["source_checksum"]
            for c in restored.bundle["check_records"]
            if (c.get("outcome_json") or {}).get("claim_index") == 10
        )
        assert outcomes[10]["decoding"]["source_checksum"] == recorded


class TestTheSamd1MappingIsRefusedOnEveryRow:
    """Study 56's recorded stop: eight rows, twelve refusals. Every row is refused for its arm's
    condition, and the four KO rows also for their clone identity."""

    @pytest.mark.asyncio
    async def test_replaying_the_mapping_reproduces_the_recorded_refusals(self, session, admin_user):
        restored = await _restored(session, admin_user, 56)
        recorded = restored.bundle["study"]["evidence_json"]["input_choice"]["mapping_validation"]
        validation = await replay_mapping(session, restored)
        assert validation["status"] == recorded["status"] == "unresolved"
        assert validation["reasons"] == recorded["reasons"]


class TestTheStiffnessPaperEndsWithScopeNotEstablished:
    """Study 57's recorded stop. Five assay types are correctly outside bioAF's methods, and that
    correct answer is obscured: the finding inventory's recovery attempt could not reach the provider,
    so the established membership was discarded and the report says the scope is not established."""

    @pytest.mark.asyncio
    async def test_replaying_the_report_reproduces_the_recorded_scorecard(self, session, admin_user):
        restored = await _restored(session, admin_user, 57)
        recorded = restored.bundle["study"]["evidence_json"]["scorecard_record"]["compact"]
        report = await replay_report(session, restored)
        card = report["scorecard"]
        assert card["display_score"] is None
        assert card["status"] == recorded["status"] == "not_established"
        assert card["cause"] == recorded["cause"] == "bioaf_limitation"
        assert "could not reach the language model" in card["reason"]

    @pytest.mark.asyncio
    async def test_the_five_assay_types_are_outside_bioafs_methods(self, session, admin_user):
        restored = await _restored(session, admin_user, 57)
        report = await replay_report(session, restored)
        assert report["applicability"]["status"] == "not_applicable"
        assert [e["support"] for e in report["applicability"]["experiments"]] == ["unsupported"] * 5
