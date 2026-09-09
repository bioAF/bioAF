"""change_7.2 section 2: a study is owned before it is read, and a late write is fenced out.

`POST /{id}/read` and the driver both ran `read_and_plan` on study 33 within three seconds. The
request's transaction committed at 10:30:27; the driver's had opened at 10:29:30, blocked on the
row, and then ran a second full extraction. Every recent study carries two plans as a result, both
back-linked, each with a full set of comparison targets, and each run paid for two extractions.

The `state != "requested"` guard could not prevent it: it is evaluated against state the other
transaction has not committed.
"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.reproduction_plan import ReproductionPlan
from app.models.validation_study import ValidationStudy
from app.services import validation_ownership as own
from app.services.validation_study_service import ValidationStudyService


@pytest_asyncio.fixture
async def other_session(db_engine):
    """A second connection, because contention between two callers is the whole subject."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


async def _study(session, admin_user):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE309060"
    )
    await session.commit()
    return study


class TestAcquisitionIsAtomic:
    @pytest.mark.asyncio
    async def test_exactly_one_of_two_callers_gets_the_claim(self, session, other_session, admin_user):
        study = await _study(session, admin_user)
        first = await own.acquire(session, study.id, holder="api")
        second = await own.acquire(other_session, study.id, holder="driver")
        assert (first is None) != (second is None)

    @pytest.mark.asyncio
    async def test_the_winner_holds_a_token_the_loser_does_not(self, session, other_session, admin_user):
        study = await _study(session, admin_user)
        claim = await own.acquire(session, study.id, holder="api")
        assert await own.held(session, claim) is True
        assert await own.acquire(other_session, study.id, holder="driver") is None

    @pytest.mark.asyncio
    async def test_a_released_claim_can_be_taken_by_the_next_caller(self, session, other_session, admin_user):
        study = await _study(session, admin_user)
        claim = await own.acquire(session, study.id, holder="api")
        await own.release(session, claim, commit=True)
        assert await own.acquire(other_session, study.id, holder="driver") is not None

    @pytest.mark.asyncio
    async def test_an_expired_claim_is_taken_over(self, session, other_session, admin_user):
        """A crashed worker must not lock a study for ever."""
        study = await _study(session, admin_user)
        await own.acquire(session, study.id, holder="crashed", lease_seconds=-1)
        assert await own.acquire(other_session, study.id, holder="driver") is not None


class TestTheFence:
    @pytest.mark.asyncio
    async def test_a_write_under_a_superseded_claim_is_rejected(self, session, other_session, admin_user):
        """The expired worker finishes late. Its result must not land over its replacement's."""
        study = await _study(session, admin_user)
        stale = await own.acquire(session, study.id, holder="worker-1", lease_seconds=-1)
        await own.acquire(other_session, study.id, holder="worker-2")
        with pytest.raises(own.ClaimLost):
            await own.assert_held(session, stale)

    @pytest.mark.asyncio
    async def test_a_write_under_a_live_claim_is_allowed(self, session, admin_user):
        study = await _study(session, admin_user)
        claim = await own.acquire(session, study.id, holder="worker-1")
        await own.assert_held(session, claim)  # does not raise

    @pytest.mark.asyncio
    async def test_a_cancellation_invalidates_the_running_claim(self, session, other_session, admin_user):
        """A cancel that leaves the claim valid can be silently undone by a late extraction."""
        study = await _study(session, admin_user)
        claim = await own.acquire(session, study.id, holder="worker-1")
        await own.invalidate(other_session, study.id)
        await other_session.commit()
        with pytest.raises(own.ClaimLost):
            await own.assert_held(session, claim)

    @pytest.mark.asyncio
    async def test_an_unowned_caller_is_not_fenced(self, session, admin_user):
        """Added underneath existing callers: refusing every unclaimed write would stop the
        application rather than protect it."""
        await own.assert_held(session, None)


class TestRenewal:
    @pytest.mark.asyncio
    async def test_a_claim_survives_work_longer_than_its_lease(self, session, other_session, admin_user):
        """Extraction has been observed at 100 seconds across two model calls, which is longer than
        any safe fixed lease."""
        study = await _study(session, admin_user)
        claim = await own.acquire(session, study.id, holder="worker-1", lease_seconds=1)
        assert await own.renew(session, claim, lease_seconds=600) is True
        await session.commit()
        assert await own.acquire(other_session, study.id, holder="worker-2") is None

    @pytest.mark.asyncio
    async def test_renewing_a_lost_claim_reports_the_loss(self, session, other_session, admin_user):
        study = await _study(session, admin_user)
        stale = await own.acquire(session, study.id, holder="worker-1", lease_seconds=-1)
        await own.acquire(other_session, study.id, holder="worker-2")
        assert await own.renew(session, stale) is False

    @pytest.mark.asyncio
    async def test_owned_renews_while_the_work_runs(self, session, other_session, admin_user):
        study = await _study(session, admin_user)
        async with own.owned(session, study.id, holder="worker-1", lease_seconds=1, renew_every=0) as claim:
            assert claim is not None
            await asyncio.sleep(0.05)
            assert await own.acquire(other_session, study.id, holder="worker-2") is None

    @pytest.mark.asyncio
    async def test_owned_yields_none_when_somebody_else_holds_it(self, session, other_session, admin_user):
        study = await _study(session, admin_user)
        await own.acquire(other_session, study.id, holder="worker-2")
        async with own.owned(session, study.id, holder="worker-1") as claim:
            assert claim is None


class TestOneExtractionPerStudy:
    @pytest.mark.asyncio
    async def test_a_second_reader_is_refused_while_the_first_holds_the_study(
        self, session, other_session, admin_user, monkeypatch
    ):
        from app.exceptions import ValidationError
        from app.services.validation_driver_service import ValidationDriverService

        study = await _study(session, admin_user)
        await own.acquire(other_session, study.id, holder="driver")

        with pytest.raises(ValidationError):
            await ValidationDriverService.read_and_plan(
                session, study, "the paper body", admin_user.organization_id, admin_user.id
            )

    @pytest.mark.asyncio
    async def test_the_refusal_says_the_work_is_already_running(self, session, other_session, admin_user):
        from app.exceptions import ValidationError
        from app.services.validation_driver_service import ValidationDriverService

        study = await _study(session, admin_user)
        await own.acquire(other_session, study.id, holder="driver")
        with pytest.raises(ValidationError) as ei:
            await ValidationDriverService.read_and_plan(
                session, study, "the paper body", admin_user.organization_id, admin_user.id
            )
        assert "already reading" in str(ei.value).lower()


class TestExternalOperationsAreNotLaunchedTwice:
    """Fencing a database write does not recall a Kubernetes job that has already launched."""

    @pytest.mark.asyncio
    async def test_an_identity_exists_before_the_dispatch(self, session, admin_user):
        study = await _study(session, admin_user)
        record = await own.begin_operation(session, study, "fetchngs", kind="data_acquisition")
        assert record["operation_id"]
        assert record["status"] == own.OP_DISPATCHING
        assert record["external_id"] is None

    @pytest.mark.asyncio
    async def test_a_retry_reuses_the_same_identity(self, session, admin_user):
        study = await _study(session, admin_user)
        first = await own.begin_operation(session, study, "fetchngs")
        second = await own.begin_operation(session, study, "fetchngs")
        assert first["operation_id"] == second["operation_id"]

    @pytest.mark.asyncio
    async def test_the_providers_identifier_is_attached_once_it_accepts(self, session, admin_user):
        study = await _study(session, admin_user)
        await own.begin_operation(session, study, "fetchngs")
        record = await own.record_dispatch(session, study, "fetchngs", external_id=99)
        assert record["external_id"] == 99
        assert record["status"] == own.OP_RUNNING

    @pytest.mark.asyncio
    async def test_a_crash_before_the_identifier_leaves_an_adoptable_operation(self, session, admin_user):
        """The window a crash lands in. A record in `dispatching` is a question to ask the provider,
        never a licence to launch another."""
        study = await _study(session, admin_user)
        await own.begin_operation(session, study, "fetchngs")
        assert own.adoptable(study, "fetchngs") is not None

    @pytest.mark.asyncio
    async def test_a_finished_operation_is_not_adoptable(self, session, admin_user):
        study = await _study(session, admin_user)
        await own.begin_operation(session, study, "fetchngs")
        await own.finish_operation(session, study, "fetchngs")
        assert own.adoptable(study, "fetchngs") is None

    @pytest.mark.asyncio
    async def test_adoption_is_recorded_rather_than_silent(self, session, admin_user):
        study = await _study(session, admin_user)
        await own.begin_operation(session, study, "fetchngs")
        record = await own.adopt(session, study, "fetchngs", by="driver")
        assert record["adopted_by"] == "driver"
        assert record["adopted_at"]

    @pytest.mark.asyncio
    async def test_dispatching_under_a_lost_claim_is_refused(self, session, other_session, admin_user):
        """Ownership is checked immediately before the dispatch, not only before the write."""
        study = await _study(session, admin_user)
        stale = await own.acquire(session, study.id, holder="worker-1", lease_seconds=-1)
        await own.acquire(other_session, study.id, holder="worker-2")
        with pytest.raises(own.ClaimLost):
            await own.begin_operation(session, study, "fetchngs", claim=stale)


class TestOneActivePlanWithItsHistoryKept:
    @pytest.mark.asyncio
    async def test_a_study_resolves_to_exactly_one_active_plan(self, session, admin_user):
        from app.services.validation_assessment import active_plan

        study = await _study(session, admin_user)
        first = ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/rnaseq")
        session.add(first)
        await session.flush()
        first.superseded_at = own._now()
        first.superseded_reason = "a second writer replaced it"
        second = ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/scrnaseq")
        session.add(second)
        study.reproduction_plan_id = second.id
        await session.flush()

        plan = await active_plan(session, study)
        assert plan.id == second.id

    @pytest.mark.asyncio
    async def test_the_superseded_plan_is_kept_as_history(self, session, admin_user):
        """Study 33's discarded plan records a different model interpretation of the same claim and
        is part of the evidence explaining that run. Deleting it would destroy the record."""
        study = await _study(session, admin_user)
        first = ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/rnaseq")
        session.add(first)
        await session.flush()
        first.superseded_at = own._now()
        second = ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/scrnaseq")
        session.add(second)
        await session.flush()

        rows = (
            await session.execute(select(ReproductionPlan).where(ReproductionPlan.validation_study_id == study.id))
        ).scalars().all()
        assert len(rows) == 2
        assert [r for r in rows if r.superseded_at is not None]

    @pytest.mark.asyncio
    async def test_two_active_plans_are_refused_by_the_schema(self, session, admin_user):
        from sqlalchemy.exc import IntegrityError

        study = await _study(session, admin_user)
        session.add(ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/rnaseq"))
        await session.flush()
        session.add(ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/scrnaseq"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    @pytest.mark.asyncio
    async def test_the_claimed_cutoffs_come_from_the_active_plan_only(self, session, admin_user):
        """`_claimed_thresholds` read the cutoffs off BOTH of study 33's plans."""
        from app.models.comparison_target import ComparisonTarget
        from app.services.validation_assessment import claimed_thresholds

        study = await _study(session, admin_user)
        superseded = ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/rnaseq")
        session.add(superseded)
        await session.flush()
        session.add(
            ComparisonTarget(
                reproduction_plan_id=superseded.id, metric_key="x", threshold=2.0, threshold_kind="abs_log2fc"
            )
        )
        superseded.superseded_at = own._now()
        active = ReproductionPlan(validation_study_id=study.id, pipeline_key="nf-core/rnaseq")
        session.add(active)
        await session.flush()
        session.add(
            ComparisonTarget(
                reproduction_plan_id=active.id, metric_key="x", threshold=1.5, threshold_kind="abs_log2fc"
            )
        )
        study.reproduction_plan_id = active.id
        await session.flush()

        assert await claimed_thresholds(session, study) == [1.5]
