"""The scientist's way out of a plan the deposit contradicts.

`library_strategy_conflict` refuses to approve a plan whose pipeline cannot read the data the study
is scoped to, which is right: study 14 was planned as `nf-core/atacseq` over Bisulfite-Seq at
`exact` confidence and nothing objected. But the refusal was a dead end. The blocker rendered as one
bullet among advisory ones, Approve stayed enabled, clicking it returned a 400, and no control
anywhere resolved it. The only remaining action was Decline, which is terminal.

Two ways out, and the order matters. Re-pointing the plan at the pipeline the deposit names fixes
the cause and is the common case. Overriding says the deposit itself is mislabelled, which happens,
so it stays available but costs a stated reason and goes on the record where a divergent verdict can
be argued against it.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.reproduction_plan import ReproductionPlan
from app.services.pipeline_mapper import deposit_conflict, library_strategy_conflict
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService

CONFLICT = library_strategy_conflict("nf-core/atacseq", "Bisulfite-Seq")
UNROUTED_CONFLICT = library_strategy_conflict("nf-core/atacseq", "RNA-Seq")
ADVISORY = "The paper names more than one reference genome (GRCh38, GRCm39). GRCh38 was taken."


async def _conflicted(session, admin_user, *, blockers=None, strategy="Bisulfite-Seq", state="plan_ready"):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1234/x", source_accession="GSE213770"
    )
    study.state = state
    plan = ReproductionPlan(
        validation_study_id=study.id,
        accessions_json=["GSE213770"],
        pipeline_key="nf-core/atacseq",
        pipeline_version="2.1.2",
        blockers_json=[ADVISORY, CONFLICT] if blockers is None else blockers,
        library_strategy=strategy,
    )
    session.add(plan)
    await session.flush()
    study.reproduction_plan_id = plan.id
    await session.flush()
    return study, plan


async def _registry_has_methylseq(session, version: str = "4.2.0"):
    """The registry cache knowing a pipeline is what makes it repointable-to: it is offered at the
    gate with an install control, exactly as nf-core/methylseq was for study 17."""
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline

    session.add(
        NfCoreRegistryPipeline(
            name="methylseq",
            full_name="nf-core/methylseq",
            description="Methylation (Bisulfite-Seq) analysis",
            topics=["methylseq", "bisulfite-sequencing"],
            latest_release=version,
            archived=False,
        )
    )
    await session.flush()


class TestTheGateCanTellWhichBlockerIsFatal:
    def test_the_conflict_names_itself_and_the_pipeline_that_would_fix_it(self):
        """The UI must not have to tell a fatal blocker from an advisory one by reading prose: it
        disables Approve on this answer."""
        found = deposit_conflict([ADVISORY, CONFLICT], "Bisulfite-Seq")
        assert found is not None
        assert found["suggested_pipeline_key"] == "nf-core/methylseq"
        assert "Bisulfite-Seq" in found["message"]

    def test_advisory_blockers_alone_are_not_a_conflict(self):
        assert deposit_conflict([ADVISORY], "Bisulfite-Seq") is None
        assert deposit_conflict([], None) is None

    def test_a_deposit_that_names_no_pipeline_still_reports_the_conflict(self):
        """RNA-Seq is declared but deliberately unrouted: it can refuse a plan without being able to
        propose one, and the gate then offers only the override."""
        found = deposit_conflict([UNROUTED_CONFLICT], "RNA-Seq")
        assert found is not None
        assert found["suggested_pipeline_key"] is None

    @pytest.mark.asyncio
    async def test_the_study_response_carries_it(self, client, admin_token, session, admin_user):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        study, _ = await _conflicted(session, admin_user)
        await session.commit()

        r = await client.get(f"/api/validation-studies/{study.id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["deposit_conflict"]["suggested_pipeline_key"] == "nf-core/methylseq"


class TestUsingThePipelineTheDepositNames:
    @pytest.mark.asyncio
    async def test_it_repoints_the_plan_and_clears_the_conflict(self, session, admin_user):
        await _registry_has_methylseq(session)
        study, plan = await _conflicted(session, admin_user)
        updated = await ReproductionPlanService.use_deposit_pipeline(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert updated.pipeline_key == "nf-core/methylseq"
        assert deposit_conflict(updated.blockers_json, updated.library_strategy) is None

    @pytest.mark.asyncio
    async def test_it_pins_the_version_this_instance_would_install(self, session, admin_user):
        """The pinned version is what the catalog installs, what makes the gate's install control
        clickable, and what a rerun reproduces, so a repointed plan must not keep the version of the
        pipeline it moved off."""
        await _registry_has_methylseq(session, "4.2.0")
        study, _ = await _conflicted(session, admin_user)
        updated = await ReproductionPlanService.use_deposit_pipeline(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert updated.pipeline_version == "4.2.0"

    @pytest.mark.asyncio
    async def test_it_refuses_rather_than_repoint_onto_a_pipeline_it_cannot_name_a_version_for(
        self, session, admin_user
    ):
        """Writing a null version would move the plan onto a pipeline the gate could not then offer
        to install, which is the dead end this whole change exists to remove."""
        study, _ = await _conflicted(session, admin_user)
        with pytest.raises(HTTPException) as ei:
            await ReproductionPlanService.use_deposit_pipeline(
                session, study.id, admin_user.organization_id, admin_user.id
            )
        assert "no version" in ei.value.detail.lower()

    @pytest.mark.asyncio
    async def test_the_advisory_blockers_survive_it(self, session, admin_user):
        """Only the conflict is resolved. The rest is still what the scientist has to weigh."""
        await _registry_has_methylseq(session)
        study, _ = await _conflicted(session, admin_user)
        updated = await ReproductionPlanService.use_deposit_pipeline(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert ADVISORY in (updated.blockers_json or [])

    @pytest.mark.asyncio
    async def test_the_repointed_plan_can_then_be_approved(self, session, admin_user):
        await _registry_has_methylseq(session)
        study, _ = await _conflicted(session, admin_user)
        await ReproductionPlanService.use_deposit_pipeline(session, study.id, admin_user.organization_id, admin_user.id)
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert approved.state == "acquiring_data"

    @pytest.mark.asyncio
    async def test_it_refuses_when_there_is_no_conflict_to_resolve(self, session, admin_user):
        study, _ = await _conflicted(session, admin_user, blockers=[ADVISORY])
        with pytest.raises(HTTPException) as ei:
            await ReproductionPlanService.use_deposit_pipeline(
                session, study.id, admin_user.organization_id, admin_user.id
            )
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_it_refuses_when_the_deposit_names_no_pipeline(self, session, admin_user):
        study, _ = await _conflicted(session, admin_user, blockers=[UNROUTED_CONFLICT], strategy="RNA-Seq")
        with pytest.raises(HTTPException) as ei:
            await ReproductionPlanService.use_deposit_pipeline(
                session, study.id, admin_user.organization_id, admin_user.id
            )
        assert "no single pipeline" in ei.value.detail.lower()

    @pytest.mark.asyncio
    async def test_it_is_not_offered_once_the_study_has_moved_on(self, session, admin_user):
        study, _ = await _conflicted(session, admin_user, state="running")
        with pytest.raises(HTTPException) as ei:
            await ReproductionPlanService.use_deposit_pipeline(
                session, study.id, admin_user.organization_id, admin_user.id
            )
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_it_leaves_the_study_at_the_gate(self, session, admin_user):
        """Re-pointing edits the plan. It must not advance, approve or classify anything."""
        await _registry_has_methylseq(session)
        study, _ = await _conflicted(session, admin_user)
        await ReproductionPlanService.use_deposit_pipeline(session, study.id, admin_user.organization_id, admin_user.id)
        assert study.state == "plan_ready"
        assert study.approved_by_user_id is None


class TestRunningItAnyway:
    @pytest.mark.asyncio
    async def test_approving_is_refused_while_the_conflict_stands(self, session, admin_user):
        study, _ = await _conflicted(session, admin_user)
        with pytest.raises(HTTPException) as ei:
            await ValidationStudyService.approve_plan(session, study.id, admin_user.organization_id, admin_user.id)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_an_override_records_who_decided_and_why(self, session, admin_user):
        study, _ = await _conflicted(session, admin_user)
        updated = await ValidationStudyService.override_deposit_conflict(
            session,
            study.id,
            admin_user.organization_id,
            admin_user.id,
            reason="the depositor labelled this series wrong; the methods are unambiguous",
        )
        override = (updated.evidence_json or {})["deposit_override"]
        assert override["user_id"] == admin_user.id
        assert "depositor labelled" in override["reason"]
        assert override["at"]

    @pytest.mark.asyncio
    async def test_the_override_is_what_lets_approval_through(self, session, admin_user):
        study, _ = await _conflicted(session, admin_user)
        await ValidationStudyService.override_deposit_conflict(
            session, study.id, admin_user.organization_id, admin_user.id, reason="deposit is mislabelled"
        )
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert approved.state == "acquiring_data"

    @pytest.mark.asyncio
    async def test_an_override_needs_a_stated_reason(self, session, admin_user):
        """A one-click override becomes the default action, and then the guard means nothing."""
        study, _ = await _conflicted(session, admin_user)
        with pytest.raises(HTTPException) as ei:
            await ValidationStudyService.override_deposit_conflict(
                session, study.id, admin_user.organization_id, admin_user.id, reason="   "
            )
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_it_refuses_when_there_is_nothing_to_override(self, session, admin_user):
        study, _ = await _conflicted(session, admin_user, blockers=[ADVISORY])
        with pytest.raises(HTTPException):
            await ValidationStudyService.override_deposit_conflict(
                session, study.id, admin_user.organization_id, admin_user.id, reason="nothing to override"
            )

    @pytest.mark.asyncio
    async def test_the_decision_survives_onto_the_running_study(self, session, admin_user):
        """A verdict that diverges has to be arguable against the choice that produced it."""
        study, _ = await _conflicted(session, admin_user)
        await ValidationStudyService.override_deposit_conflict(
            session, study.id, admin_user.organization_id, admin_user.id, reason="deposit is mislabelled"
        )
        approved = await ValidationStudyService.approve_plan(
            session, study.id, admin_user.organization_id, admin_user.id
        )
        assert (approved.evidence_json or {})["deposit_override"]["reason"] == "deposit is mislabelled"

    @pytest.mark.asyncio
    async def test_it_is_audited(self, session, admin_user):
        study, _ = await _conflicted(session, admin_user)
        await ValidationStudyService.override_deposit_conflict(
            session, study.id, admin_user.organization_id, admin_user.id, reason="deposit is mislabelled"
        )
        await session.flush()
        actions = (
            (
                await session.execute(
                    select(AuditLog.action).where(
                        AuditLog.entity_type == "validation_study", AuditLog.entity_id == study.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "deposit_conflict_overridden" in actions
