"""plan_8_1 section 1.4, decision D6: the migration that recovers studies whose read failed.

It moves only ``classified`` studies the failed-read rule matches, to ``error``, audited, keeping the prior
state, classification and failure reason in history. Retry then reads each again. It uses the same rule
every surface renders with, so it moves nothing that rule does not match.
"""

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.validation_study import ValidationStudy
from app.models.validation_study_issue import ValidationStudyIssue
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_read_recovery import recover_failed_reads
from app.services.validation_study_service import ValidationStudyService

_READING = "reading the paper and extracting its methods and claims"
_ABSENCE = ["no data accession found in the paper", "insufficient method detail to identify an assay"]


async def _study(
    session, user, *, state="classified", classification="missing_data", evidence=None, claims=0, issue=True
):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, intended_route="deposit")
    plan = await ReproductionPlanService.create_plan(session, study, user.id, accessions=[], blockers=list(_ABSENCE))
    if claims:
        await ReproductionPlanService.add_comparison_targets(
            session, plan, [{"metric_key": "", "claim_text": f"claim {i}"} for i in range(claims)]
        )
    if issue:
        session.add(
            ValidationStudyIssue(
                validation_study_id=study.id, step=_READING, outcome="truncated", impact="blocked", message="cut off"
            )
        )
    study.state = state
    study.classification = classification
    study.failure_reason = "; ".join(_ABSENCE)
    study.evidence_json = evidence or {}
    await session.commit()
    return study.id


async def _recover(session) -> list[int]:
    moved = await session.run_sync(lambda sync: recover_failed_reads(sync.connection()))
    await session.commit()
    return moved


async def _load(session, study_id):
    session.expire_all()
    return (await session.execute(select(ValidationStudy).where(ValidationStudy.id == study_id))).scalar_one()


class TestTheMigration:
    @pytest.mark.asyncio
    async def test_a_study_shaped_like_42_moves_to_error_with_its_history(self, session, admin_user):
        study_id = await _study(session, admin_user)
        assert await _recover(session) == [study_id]
        study = await _load(session, study_id)
        assert study.state == "error"
        assert study.classification is None
        assert study.failure_reason.startswith("bioAF could not read the paper: ")
        (history,) = study.evidence_json["recovery_history"]
        assert history["prior_state"] == "classified"
        assert history["prior_classification"] == "missing_data"
        assert history["prior_failure_reason"] == "; ".join(_ABSENCE)

    @pytest.mark.asyncio
    async def test_the_move_is_audited(self, session, admin_user):
        study_id = await _study(session, admin_user)
        await _recover(session)
        entry = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "validation_study",
                        AuditLog.entity_id == study_id,
                        AuditLog.action == "state_change",
                    )
                )
            )
            .scalars()
            .all()[-1]
        )
        assert entry.details_json["state"] == "error"
        assert entry.details_json["recovered_failed_read"] is True
        assert entry.previous_value_json["classification"] == "missing_data"

    @pytest.mark.asyncio
    async def test_it_moves_nothing_the_rule_does_not_match(self, session, admin_user):
        with_claims = await _study(session, admin_user, claims=2)
        no_issue = await _study(session, admin_user, issue=False)
        read_succeeded = await _study(
            session, admin_user, evidence={"extraction": {"status": "succeeded", "attempts": []}}
        )
        not_classified = await _study(session, admin_user, state="error", classification=None)
        assert await _recover(session) == []
        for study_id, state in (
            (with_claims, "classified"),
            (no_issue, "classified"),
            (read_succeeded, "classified"),
            (not_classified, "error"),
        ):
            assert (await _load(session, study_id)).state == state

    @pytest.mark.asyncio
    async def test_retry_then_reads_the_recovered_study_again(self, session, admin_user):
        study_id = await _study(session, admin_user)
        await _recover(session)
        study = await ValidationStudyService.retry_study(session, study_id, admin_user.organization_id, admin_user.id)
        assert study.state == "requested"

    def test_the_migration_calls_the_rule(self):
        import importlib.util
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "142_recover_failed_reads.py"
        spec = importlib.util.spec_from_file_location("migration_142", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "142" and module.down_revision == "141"
        assert module.recover_failed_reads is recover_failed_reads
