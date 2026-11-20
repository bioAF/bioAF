"""Tests for the agent_review_job service (ADR-055, spec-llm-integration-jobs).

Behaviors verified:
- create() snapshots the active provider, model, and template version onto
  both the job and review rows.
- create() raises NoActiveProvider when there is no active config.
- create() raises JobAlreadyRunning when the debounce partial unique index
  trips, and exposes the existing job and review ids.
- Once an in-flight job terminates, a new create succeeds for the same
  (entity, review_type).
- A different (entity, review_type) can run concurrently with an in-flight job.
- mark_orphaned_on_startup transitions in-flight hosted jobs to failed
  (process_restart) and leaves Gemma jobs to their own reconciliation.
- create() refuses a provider with no review execution path (Gemma) before
  reaping or writing anything.
- fail_unsupported_on_startup fails every in-flight Gemma job and its linked
  nonterminal review together (unsupported_review_execution, one audit row),
  across organizations, idempotently, preserving terminal records, rolling
  back on failure, and releasing the in-flight uniqueness block.
- execute_hosted happy path: artifact built, response submitted, parsed, both
  rows transition to succeeded, two audit rows written
  (llm_review_submitted, llm_review_succeeded), artifact_gcs_paths populated.
- execute_hosted provider error path: review and job land in 'failed',
  audit row has error_class=provider_error.
- execute_hosted artifact-build failure path: no submitted audit row, one
  failed audit row with error_class=artifact_build_failure, no GCS paths in
  the audit row.
- execute_hosted parse-failure path: succeeded job + review, severity=unknown,
  audit row has parse_failure=true.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent_review import AgentReview
from app.models.agent_review_job import AgentReviewJob
from app.models.audit_log import AuditLog
from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun, PipelineRunSample
from app.models.sample import Sample
from app.services import agent_review_job_service as job_service
from app.services import llm_provider_config_service
from app.services.agent_review_prompt_builder import (
    PIPELINE_RUN_REVIEW_V2_BUILDER_NAME,
)
from app.services.llm_provider_clients import ProviderError


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _make_pipeline_run(session: AsyncSession, org_id: int, with_output: bool = True) -> int:
    exp = Experiment(name="E1", organization_id=org_id, status="processing")
    session.add(exp)
    await session.flush()
    run = PipelineRun(
        organization_id=org_id,
        experiment_id=exp.id,
        pipeline_name="rnaseq",
        pipeline_version="3.14",
        parameters_json={"genome": "GRCh38"},
        output_files_json={"counts": "gs://x/y.tsv"} if with_output else None,
        status="complete",
    )
    session.add(run)
    await session.flush()
    s1 = Sample(experiment_id=exp.id, external_id="EXT-1", tissue_type="liver", qc_status="pass")
    session.add(s1)
    await session.flush()
    session.add(PipelineRunSample(pipeline_run_id=run.id, sample_id=s1.id))
    await session.commit()
    return run.id


async def _configure_active_provider(
    session: AsyncSession, org_id: int, user_id: int, *, provider: str = "openai"
) -> None:
    await llm_provider_config_service.upsert(
        session,
        org_id=org_id,
        provider=provider,
        api_key="sk-test-LAST5" if provider != "gemma" else None,
        model=f"{provider}-test-model",
        actor_user_id=user_id,
    )
    await llm_provider_config_service.set_active(session, org_id=org_id, provider=provider, actor_user_id=user_id)
    await session.commit()


@pytest.mark.asyncio
async def test_create_snapshots_active_provider(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)

    async with _factory(db_engine)() as session:
        job, review = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        assert job.provider == "openai"
        assert job.model == "openai-test-model"
        assert job.prompt_template_version == PIPELINE_RUN_REVIEW_V2_BUILDER_NAME
        assert job.prompt_source == "builder"
        assert job.prompt_text is not None and "## Quality control" in job.prompt_text
        assert job.prompt_sections == ["qc.metric_review"]
        assert job.status == "pending"
        assert review.status == "pending"
        assert review.agent_review_job_id == job.id
        assert job.agent_review_id == review.id


@pytest.mark.asyncio
async def test_create_raises_when_no_active_provider(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
    async with _factory(db_engine)() as session:
        with pytest.raises(job_service.NoActiveProvider):
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="pipeline_run",
                entity_id=run_id,
                selected_sub_item_ids=["qc.metric_review"],
            )


async def _job_and_review_counts(session: AsyncSession) -> tuple[int, int]:
    from sqlalchemy import func

    jobs = (await session.execute(select(func.count(AgentReviewJob.id)))).scalar_one()
    reviews = (await session.execute(select(func.count(AgentReview.id)))).scalar_one()
    return jobs, reviews


@pytest.mark.asyncio
async def test_create_rejects_provider_without_review_execution(db_engine, admin_user, monkeypatch):
    """Gemma has no review executor, so create refuses it before writing anything, with a
    precondition error distinct from "no provider configured". No provider is called."""
    from app.services.llm_provider_clients import gemma_client

    async def must_not_submit(**kwargs):
        raise AssertionError("an unsupported provider must never be called")

    monkeypatch.setattr(gemma_client, "submit", must_not_submit)
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id, provider="gemma")
        run_id = await _make_pipeline_run(session, admin_user.organization_id)

    async with _factory(db_engine)() as session:
        with pytest.raises(job_service.UnsupportedReviewExecution) as excinfo:
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="pipeline_run",
                entity_id=run_id,
                selected_sub_item_ids=["qc.metric_review"],
            )
        # Same precondition family the endpoint maps to 412, with its own explanation.
        assert isinstance(excinfo.value, job_service.NoActiveProvider)
        message = str(excinfo.value)
        assert "Agent Review" in message
        assert "no active" not in message.lower()
        await session.rollback()

    async with _factory(db_engine)() as session:
        assert await _job_and_review_counts(session) == (0, 0)


@pytest.mark.asyncio
async def test_create_rejection_leaves_stale_in_flight_job_untouched(db_engine, admin_user):
    """Admission is checked before the stale-job reaper: a rejected request mutates nothing."""
    from datetime import UTC, datetime, timedelta

    from app.services.agent_review_prompt_builder import template_name_for_scope

    review_type = template_name_for_scope(False)
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id, provider="gemma")
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        stale = AgentReviewJob(
            organization_id=admin_user.organization_id,
            triggered_by_user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            review_type=review_type,
            provider="openai",
            model="openai-test-model",
            prompt_template_version=review_type,
            status="pending",
            prompt_text="stub",
            created_at=datetime.now(UTC) - timedelta(minutes=30),
        )
        session.add(stale)
        await session.commit()
        stale_id = stale.id

    async with _factory(db_engine)() as session:
        with pytest.raises(job_service.UnsupportedReviewExecution):
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="pipeline_run",
                entity_id=run_id,
                selected_sub_item_ids=["qc.metric_review"],
            )
        await session.rollback()

    async with _factory(db_engine)() as session:
        loaded = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == stale_id))).scalar_one()
        assert loaded.status == "pending"
        assert loaded.error_class is None
        assert await _job_and_review_counts(session) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target", ["foreign_run", "foreign_experiment", "foreign_included_run", "nonmember_included_run"]
)
async def test_create_refuses_entities_outside_the_org_or_experiment(db_engine, admin_user, target):
    """Review admission enforces the organization boundary before any write: a foreign run or
    experiment is not found, and an experiment review may include only that experiment's own
    runs. Nothing is reaped, written, or sent to a provider."""
    from app.exceptions import NotFoundError, ValidationError

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        foreign_user = await _second_org_user(session)
        foreign_run_id = await _make_pipeline_run(session, foreign_user.organization_id)
        foreign_run = await session.get(PipelineRun, foreign_run_id)
        foreign_exp_id = foreign_run.experiment_id
        own_run_id = await _make_pipeline_run(session, admin_user.organization_id)
        own_exp_id = (await session.get(PipelineRun, own_run_id)).experiment_id
        other_own_run_id = await _make_pipeline_run(session, admin_user.organization_id)

    if target == "foreign_run":
        kwargs = {"entity_type": "pipeline_run", "entity_id": foreign_run_id}
        expected = NotFoundError
    elif target == "foreign_experiment":
        kwargs = {"entity_type": "experiment", "entity_id": foreign_exp_id, "included_run_ids": []}
        expected = NotFoundError
    elif target == "foreign_included_run":
        kwargs = {"entity_type": "experiment", "entity_id": own_exp_id, "included_run_ids": [foreign_run_id]}
        expected = ValidationError
    else:
        kwargs = {"entity_type": "experiment", "entity_id": own_exp_id, "included_run_ids": [other_own_run_id]}
        expected = ValidationError

    async with _factory(db_engine)() as session:
        with pytest.raises(expected):
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                selected_sub_item_ids=["qc.metric_review"],
                **kwargs,
            )
        await session.rollback()

    async with _factory(db_engine)() as session:
        assert await _job_and_review_counts(session) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["included_run_ids", "include_html_report_run_ids"])
async def test_create_run_review_accepts_only_its_own_run_ids(db_engine, admin_user, field):
    """A single-run review covers only that run: any other run id (even an owned one) is refused."""
    from app.exceptions import ValidationError

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        other_run_id = await _make_pipeline_run(session, admin_user.organization_id)

    async with _factory(db_engine)() as session:
        with pytest.raises(ValidationError):
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="pipeline_run",
                entity_id=run_id,
                selected_sub_item_ids=["qc.metric_review"],
                **{field: [other_run_id]},
            )
        await session.rollback()
    async with _factory(db_engine)() as session:
        assert await _job_and_review_counts(session) == (0, 0)


@pytest.mark.asyncio
async def test_create_experiment_html_report_runs_must_be_included_runs(db_engine, admin_user):
    from app.exceptions import ValidationError

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        foreign_user = await _second_org_user(session)
        foreign_run_id = await _make_pipeline_run(session, foreign_user.organization_id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        exp_id = (await session.get(PipelineRun, run_id)).experiment_id

    async with _factory(db_engine)() as session:
        with pytest.raises(ValidationError):
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="experiment",
                entity_id=exp_id,
                included_run_ids=[run_id],
                include_html_report_run_ids=[foreign_run_id],
                selected_sub_item_ids=["qc.metric_review"],
            )
        await session.rollback()
    async with _factory(db_engine)() as session:
        assert await _job_and_review_counts(session) == (0, 0)


@pytest.mark.asyncio
async def test_create_owned_experiment_review_with_member_runs_succeeds(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        exp_id = (await session.get(PipelineRun, run_id)).experiment_id

    async with _factory(db_engine)() as session:
        job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="experiment",
            entity_id=exp_id,
            included_run_ids=[run_id],
            include_html_report_run_ids=[run_id],
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        assert job.included_run_ids == [run_id]


@pytest.mark.asyncio
async def test_create_never_touches_or_reveals_another_orgs_in_flight_job(db_engine, admin_user):
    """The in-flight lookup is organization-scoped: a stale in-flight row owned by another
    organization (here pointing at this org's run, as a corrupt/legacy row would) is neither
    reaped nor reported back by id."""
    from datetime import UTC, datetime, timedelta

    from app.services.agent_review_prompt_builder import template_name_for_scope

    review_type = template_name_for_scope(False)
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        foreign_user = await _second_org_user(session)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        foreign_job = AgentReviewJob(
            organization_id=foreign_user.organization_id,
            triggered_by_user_id=foreign_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            review_type=review_type,
            provider="openai",
            model="openai-test-model",
            prompt_template_version=review_type,
            status="pending",
            prompt_text="foreign",
            created_at=datetime.now(UTC) - timedelta(minutes=30),
        )
        session.add(foreign_job)
        await session.commit()
        foreign_job_id = foreign_job.id

    async with _factory(db_engine)() as session:
        with pytest.raises(job_service.JobAlreadyRunning) as excinfo:
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="pipeline_run",
                entity_id=run_id,
                selected_sub_item_ids=["qc.metric_review"],
            )
        assert excinfo.value.existing_job_id != foreign_job_id
        await session.rollback()

    async with _factory(db_engine)() as session:
        loaded = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == foreign_job_id))).scalar_one()
        assert loaded.status == "pending"
        assert loaded.error_class is None


@pytest.mark.asyncio
async def test_debounce_raises_job_already_running(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)

    async with _factory(db_engine)() as session:
        first, first_review = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        first_id = first.id
        first_review_id = first_review.id

    async with _factory(db_engine)() as session:
        with pytest.raises(job_service.JobAlreadyRunning) as exc_info:
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="pipeline_run",
                entity_id=run_id,
                selected_sub_item_ids=["qc.metric_review"],
            )
        assert exc_info.value.existing_job_id == first_id
        assert exc_info.value.existing_agent_review_id == first_review_id


@pytest.mark.asyncio
async def test_debounce_releases_after_terminal(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)

    async with _factory(db_engine)() as session:
        job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        # Move it to a terminal state manually to release the debounce.
        job.status = "failed"
        await session.commit()

    async with _factory(db_engine)() as session:
        # Now a new create on the same (entity, review_type) succeeds.
        job2, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        assert job2.id != job.id


@pytest.mark.asyncio
async def test_concurrent_button_a_and_button_b_allowed(db_engine, admin_user):
    """Button A on Run X and Button B on Experiment containing Run X can both run."""
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        # Find the experiment for that run to use as the Button B target.
        run = (await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))).scalar_one()
        exp_id = run.experiment_id

    async with _factory(db_engine)() as session:
        a, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        b, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="experiment",
            entity_id=exp_id,
            selected_sub_item_ids=["qc.metric_review", "xsample.drift_over_time"],
            included_run_ids=[run_id],
        )
        await session.commit()
        assert a.id != b.id


@pytest.mark.asyncio
async def test_mark_orphaned_transitions_inflight_hosted_to_failed(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job, review = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job.status = "submitted"
        await session.commit()
        job_id = job.id
        review_id = review.id

    async with _factory(db_engine)() as session:
        count = await job_service.mark_orphaned_on_startup(session)
        await session.commit()
        assert count == 1
        loaded_job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        loaded_review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        assert loaded_job.status == "failed"
        assert loaded_job.error_class == "process_restart"
        assert loaded_review.status == "failed"


async def _seed_legacy_gemma_job(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    run_id: int,
    status: str = "pending",
    with_review: bool = True,
    review_status: str | None = None,
) -> tuple[int, int | None]:
    """Seed the job (and linked review) rows an earlier release left for a Gemma review.

    create() now refuses Gemma, so legacy rows are written directly, mirroring what create
    used to produce."""
    from app.services.agent_review_prompt_builder import template_name_for_scope

    review_type = template_name_for_scope(False)
    job = AgentReviewJob(
        organization_id=org_id,
        triggered_by_user_id=user_id,
        entity_type="pipeline_run",
        entity_id=run_id,
        review_type=review_type,
        provider="gemma",
        model="gemma-test-model",
        prompt_template_version=review_type,
        status=status,
        prompt_text="legacy prompt",
        artifact_gcs_paths=["gs://bucket/legacy/artifact.json"],
    )
    session.add(job)
    await session.flush()
    review_id: int | None = None
    if with_review:
        review = AgentReview(
            organization_id=org_id,
            triggered_by_user_id=user_id,
            entity_type="pipeline_run",
            entity_id=run_id,
            review_type=review_type,
            provider="gemma",
            model="gemma-test-model",
            prompt_template_version=review_type,
            status=review_status or ("pending" if status in job_service.IN_FLIGHT_STATUSES else status),
            agent_review_job_id=job.id,
            prompt_text="legacy prompt",
        )
        session.add(review)
        await session.flush()
        job.agent_review_id = review.id
        review_id = review.id
    await session.commit()
    return job.id, review_id


async def _second_org_user(session: AsyncSession):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Other Org", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="admin@other.test",
        password_hash="x",
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


async def _failed_audit_count(session: AsyncSession, job_id: int) -> int:
    from sqlalchemy import func

    return (
        await session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_type == "agent_review_job",
                AuditLog.entity_id == job_id,
                AuditLog.action == "llm_review_failed",
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_startup_fails_stranded_gemma_job_and_review(db_engine, admin_user):
    """A Gemma job has no executor, so startup recovery fails it and its linked review together,
    with its own error class, one audit outcome, and the artifact references preserved."""
    async with _factory(db_engine)() as session:
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job_id, review_id = await _seed_legacy_gemma_job(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, run_id=run_id, status="submitted"
        )

    async with _factory(db_engine)() as session:
        count = await job_service.fail_unsupported_on_startup(session)
        await session.commit()
        assert count == 1

    async with _factory(db_engine)() as session:
        job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        assert job.status == "failed"
        assert job.error_class == "unsupported_review_execution"
        assert job.completed_at is not None
        assert job.artifact_gcs_paths == ["gs://bucket/legacy/artifact.json"]
        assert review.status == "failed"
        assert review.error_text and "Agent Review" in review.error_text
        assert review.completed_at is not None
        assert job.provider == "gemma" and review.provider == "gemma"
        assert await _failed_audit_count(session, job_id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "building_artifacts", "submitted"])
async def test_startup_reconciles_every_in_flight_gemma_status_across_orgs(db_engine, admin_user, status):
    """Every in-flight status is reconciled at once (no stale threshold), in every organization,
    and the in-flight uniqueness block is released so a new review can be created."""
    async with _factory(db_engine)() as session:
        other = await _second_org_user(session)
        seeded = []
        for user in (admin_user, other):
            run_id = await _make_pipeline_run(session, user.organization_id)
            job_id, review_id = await _seed_legacy_gemma_job(
                session, org_id=user.organization_id, user_id=user.id, run_id=run_id, status=status
            )
            seeded.append((user, run_id, job_id, review_id))

    async with _factory(db_engine)() as session:
        assert await job_service.fail_unsupported_on_startup(session) == 2
        await session.commit()

    async with _factory(db_engine)() as session:
        for _user, _run_id, job_id, review_id in seeded:
            job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
            review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
            assert (job.status, review.status) == ("failed", "failed")
            assert await _failed_audit_count(session, job_id) == 1

    # The uniqueness block is gone: a supported provider can now review the same run at once.
    user, run_id, _job_id, _review_id = seeded[0]
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, user.organization_id, user.id)
        new_job, _ = await job_service.create(
            session,
            org_id=user.organization_id,
            user_id=user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        assert new_job.status == "pending"


@pytest.mark.asyncio
async def test_startup_gemma_reconciliation_is_idempotent(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job_id, review_id = await _seed_legacy_gemma_job(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, run_id=run_id
        )

    async with _factory(db_engine)() as session:
        assert await job_service.fail_unsupported_on_startup(session) == 1
        await session.commit()
    async with _factory(db_engine)() as session:
        first_job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        first_review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        stamps = (first_job.completed_at, first_review.completed_at)

    async with _factory(db_engine)() as session:
        assert await job_service.fail_unsupported_on_startup(session) == 0
        await session.commit()
    async with _factory(db_engine)() as session:
        job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        assert (job.completed_at, review.completed_at) == stamps
        assert await _failed_audit_count(session, job_id) == 1


@pytest.mark.asyncio
async def test_startup_gemma_reconciliation_handles_missing_review(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job_id, review_id = await _seed_legacy_gemma_job(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            run_id=run_id,
            with_review=False,
        )
        assert review_id is None

    async with _factory(db_engine)() as session:
        assert await job_service.fail_unsupported_on_startup(session) == 1
        await session.commit()
    async with _factory(db_engine)() as session:
        job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        assert job.status == "failed"
        assert job.error_class == "unsupported_review_execution"
        assert await _failed_audit_count(session, job_id) == 1


@pytest.mark.asyncio
async def test_startup_gemma_reconciliation_preserves_terminal_records(db_engine, admin_user):
    """Already terminal Gemma jobs are left alone, and a terminal review linked to a still
    in-flight job keeps its outcome while the job is released."""
    async with _factory(db_engine)() as session:
        run_a = await _make_pipeline_run(session, admin_user.organization_id)
        run_b = await _make_pipeline_run(session, admin_user.organization_id)
        done_job, done_review = await _seed_legacy_gemma_job(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, run_id=run_a, status="failed"
        )
        stuck_job, terminal_review = await _seed_legacy_gemma_job(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            run_id=run_b,
            status="submitted",
            review_status="succeeded",
        )
    async with _factory(db_engine)() as session:
        before = (await session.execute(select(AgentReview).where(AgentReview.id == terminal_review))).scalar_one()
        before_completed = before.completed_at

    async with _factory(db_engine)() as session:
        assert await job_service.fail_unsupported_on_startup(session) == 1
        await session.commit()

    async with _factory(db_engine)() as session:
        done = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == done_job))).scalar_one()
        assert done.status == "failed" and done.error_class is None
        assert await _failed_audit_count(session, done_job) == 0
        untouched = (await session.execute(select(AgentReview).where(AgentReview.id == done_review))).scalar_one()
        assert untouched.status == "failed" and untouched.error_text is None
        stuck = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == stuck_job))).scalar_one()
        assert stuck.status == "failed"
        kept = (await session.execute(select(AgentReview).where(AgentReview.id == terminal_review))).scalar_one()
        assert kept.status == "succeeded"
        assert kept.completed_at == before_completed


@pytest.mark.asyncio
async def test_startup_gemma_reconciliation_rolls_back_and_retries(db_engine, admin_user, monkeypatch):
    """A failure mid-recovery leaves nothing half-written; the next run completes it once."""
    from app.services import audit_service

    async with _factory(db_engine)() as session:
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job_id, review_id = await _seed_legacy_gemma_job(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, run_id=run_id
        )

    real_log = audit_service.log_action

    async def broken(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(audit_service, "log_action", broken)
    async with _factory(db_engine)() as session:
        with pytest.raises(RuntimeError):
            await job_service.fail_unsupported_on_startup(session)
        await session.rollback()

    async with _factory(db_engine)() as session:
        job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        assert (job.status, review.status) == ("pending", "pending")

    monkeypatch.setattr(audit_service, "log_action", real_log)
    async with _factory(db_engine)() as session:
        assert await job_service.fail_unsupported_on_startup(session) == 1
        await session.commit()
    async with _factory(db_engine)() as session:
        job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        assert job.status == "failed"
        assert await _failed_audit_count(session, job_id) == 1


@pytest.mark.asyncio
async def test_hosted_orphan_recovery_leaves_gemma_to_its_own_reconciliation(db_engine, admin_user):
    """Hosted restart recovery still owns only providers with an executor: a Gemma job is not
    mislabeled process_restart."""
    async with _factory(db_engine)() as session:
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job_id, _ = await _seed_legacy_gemma_job(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, run_id=run_id, status="submitted"
        )

    async with _factory(db_engine)() as session:
        assert await job_service.mark_orphaned_on_startup(session) == 0
        await session.commit()
        loaded = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        assert loaded.status == "submitted"


@pytest.mark.asyncio
async def test_startup_step_runs_hosted_and_gemma_recovery(db_engine, admin_user, monkeypatch):
    """The startup owner runs both recoveries; a Gemma reconciliation failure surfaces to the
    step (which logs it) without undoing the hosted recovery already committed."""
    from contextlib import AsyncExitStack

    from app import startup

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_a = await _make_pipeline_run(session, admin_user.organization_id)
        hosted, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_a,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        hosted_id = hosted.id
        run_b = await _make_pipeline_run(session, admin_user.organization_id)
        gemma_id, _ = await _seed_legacy_gemma_job(
            session, org_id=admin_user.organization_id, user_id=admin_user.id, run_id=run_b
        )

    ctx = startup.StartupContext(engine=db_engine, stack=AsyncExitStack(), subscriptions=AsyncExitStack())

    async def broken(session):
        raise RuntimeError("reconcile failed")

    real = job_service.fail_unsupported_on_startup
    monkeypatch.setattr(job_service, "fail_unsupported_on_startup", broken)
    with pytest.raises(RuntimeError):
        await startup.hosted_review_orphans(ctx)

    async with _factory(db_engine)() as session:
        h = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == hosted_id))).scalar_one()
        g = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == gemma_id))).scalar_one()
        assert h.status == "failed" and h.error_class == "process_restart"
        assert g.status == "pending"

    monkeypatch.setattr(job_service, "fail_unsupported_on_startup", real)
    await startup.hosted_review_orphans(ctx)
    async with _factory(db_engine)() as session:
        g = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == gemma_id))).scalar_one()
        assert g.status == "failed" and g.error_class == "unsupported_review_execution"


@pytest.mark.asyncio
async def test_execute_hosted_happy_path_writes_two_audit_rows(db_engine, admin_user):
    captured: dict[str, str] = {}

    async def writer(path: str, content: str) -> None:
        captured[path] = content

    async def submit(*, prompt, payload, model, api_key):
        return '```json\n{"severity": "green", "headline": "All good"}\n```\nfree-text body here'

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job, review = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job_id = job.id
        review_id = review.id

    factory = _factory(db_engine)
    await job_service.execute_hosted(
        factory,
        job_id=job_id,
        gcs_writer=writer,
        submit_override=submit,
    )

    async with factory() as session:
        loaded_job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        loaded_review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        assert loaded_job.status == "succeeded"
        assert loaded_review.status == "succeeded"
        assert loaded_review.severity == "green"
        assert loaded_review.headline == "All good"
        assert loaded_review.artifact_gcs_paths and loaded_review.artifact_gcs_paths[0] in captured
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "agent_review_job",
                        AuditLog.entity_id == job_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        actions = sorted(a.action for a in audits)
        assert actions == ["llm_review_submitted", "llm_review_succeeded"]
        submitted = next(a for a in audits if a.action == "llm_review_submitted")
        assert submitted.details_json["provider"] == "openai"
        assert submitted.details_json["model"] == "openai-test-model"
        assert submitted.details_json["api_key_prefix_last5"] == "LAST5"
        assert submitted.details_json["artifact_gcs_paths"]
        succeeded = next(a for a in audits if a.action == "llm_review_succeeded")
        assert succeeded.details_json["severity"] == "green"
        assert succeeded.details_json["parse_failure"] is False


@pytest.mark.asyncio
async def test_execute_hosted_provider_error_writes_failed_audit(db_engine, admin_user):
    async def writer(path, content):
        return None

    async def submit(*, prompt, payload, model, api_key):
        raise ProviderError("server is on fire", error_class="server")

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job_id = job.id

    factory = _factory(db_engine)
    await job_service.execute_hosted(factory, job_id=job_id, gcs_writer=writer, submit_override=submit)

    async with factory() as session:
        loaded = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        assert loaded.status == "failed"
        assert loaded.error_class == "provider_error"
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "agent_review_job",
                        AuditLog.entity_id == job_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        actions = sorted(a.action for a in audits)
        # The submitted row IS written because the artifact was built and the
        # call was made (and only then failed); per the audit spec the failure
        # after submission yields both rows.
        assert actions == ["llm_review_failed", "llm_review_submitted"]
        failed = next(a for a in audits if a.action == "llm_review_failed")
        assert failed.details_json["error_class"] == "provider_error"


@pytest.mark.asyncio
async def test_execute_hosted_artifact_build_failure(db_engine, admin_user):
    async def writer(path, content):
        return None

    async def submit(*, prompt, payload, model, api_key):
        raise AssertionError("must not be called when artifact build fails")

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        # No output JSON: artifact build will fail.
        run_id = await _make_pipeline_run(session, admin_user.organization_id, with_output=False)
        job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job_id = job.id

    factory = _factory(db_engine)
    await job_service.execute_hosted(factory, job_id=job_id, gcs_writer=writer, submit_override=submit)

    async with factory() as session:
        loaded = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        assert loaded.status == "failed"
        assert loaded.error_class == "artifact_build_failure"
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "agent_review_job",
                        AuditLog.entity_id == job_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        actions = [a.action for a in audits]
        # No submitted row: nothing was sent.
        assert actions == ["llm_review_failed"]
        failed = audits[0]
        assert failed.details_json["error_class"] == "artifact_build_failure"


@pytest.mark.asyncio
async def test_execute_hosted_parse_failure_succeeds_with_marker(db_engine, admin_user):
    async def writer(path, content):
        return None

    async def submit(*, prompt, payload, model, api_key):
        return "no json header at all, just text"

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job, review = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job_id = job.id
        review_id = review.id

    factory = _factory(db_engine)
    await job_service.execute_hosted(factory, job_id=job_id, gcs_writer=writer, submit_override=submit)

    async with factory() as session:
        loaded_job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        loaded_review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        assert loaded_job.status == "succeeded"
        assert loaded_review.severity == "unknown"
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_type == "agent_review_job",
                        AuditLog.entity_id == job_id,
                        AuditLog.action == "llm_review_succeeded",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert audits[0].details_json["parse_failure"] is True
        assert audits[0].details_json["severity"] == "unknown"


@pytest.mark.asyncio
async def test_create_with_custom_prompt_id_uses_saved_body(db_engine, admin_user):
    """custom_prompt_id resolves to the saved prompt body verbatim."""
    from app.services import agent_review_prompt_service

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        saved = await agent_review_prompt_service.create(
            session,
            org_id=admin_user.organization_id,
            name="Std",
            body="--- custom saved prompt body ---",
            created_by_user_id=admin_user.id,
        )
        await session.commit()
        saved_id = saved.id

    async with _factory(db_engine)() as session:
        job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            custom_prompt_id=saved_id,
        )
        await session.commit()
        assert job.prompt_source == "custom_saved"
        assert job.prompt_custom_id == saved_id
        assert job.prompt_text == "--- custom saved prompt body ---"
        assert job.prompt_sections is None


@pytest.mark.asyncio
async def test_create_with_custom_prompt_body_one_off(db_engine, admin_user):
    """custom_prompt_body uses the literal body and does not persist a saved row."""
    from app.models.agent_review_prompt import AgentReviewPrompt

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)

    async with _factory(db_engine)() as session:
        job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            custom_prompt_body="--- one-off body ---",
        )
        await session.commit()
        assert job.prompt_source == "custom_one_off"
        assert job.prompt_custom_id is None
        assert job.prompt_text == "--- one-off body ---"

        # No new AgentReviewPrompt row was created.
        rows = (await session.execute(select(AgentReviewPrompt))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_create_requires_at_least_one_prompt_source(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)

    async with _factory(db_engine)() as session:
        from app.services.agent_review_prompt_builder import EmptySectionSelection

        with pytest.raises(EmptySectionSelection):
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="pipeline_run",
                entity_id=run_id,
                selected_sub_item_ids=[],
            )


@pytest.mark.asyncio
async def test_execute_hosted_provider_error_with_empty_message_persists_placeholder(db_engine, admin_user):
    """An empty ProviderError message must not surface as a blank error in the UI.

    The persisted error_text on both job and review rows must fall back to a
    string that at least references the error_class, so the failed-card modal
    is never empty. The audit row's error_text mirrors that fallback.
    """

    async def writer(path, content):
        return None

    async def submit(*, prompt, payload, model, api_key):
        raise ProviderError("", error_class="transport")

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job, review = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job_id = job.id
        review_id = review.id

    factory = _factory(db_engine)
    await job_service.execute_hosted(factory, job_id=job_id, gcs_writer=writer, submit_override=submit)

    async with factory() as session:
        loaded_job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        loaded_review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        assert loaded_job.status == "failed"
        assert loaded_job.error_text and loaded_job.error_text.strip() != ""
        assert "transport" in loaded_job.error_text
        assert loaded_review.status == "failed"
        assert loaded_review.error_text and loaded_review.error_text.strip() != ""
        assert "transport" in loaded_review.error_text

        failed_audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "agent_review_job",
                    AuditLog.entity_id == job_id,
                    AuditLog.action == "llm_review_failed",
                )
            )
        ).scalar_one()
        assert failed_audit.details_json["error_text"]
        assert "transport" in failed_audit.details_json["error_text"]


@pytest.mark.asyncio
async def test_execute_hosted_emits_lifecycle_logs(db_engine, admin_user, caplog):
    """execute_hosted must emit log records at start, submit, and parse stages.

    Without these, a background-task failure is invisible in the container logs.
    """
    import logging

    caplog.set_level(logging.INFO, logger="bioaf.agent_review_job")

    async def writer(path, content):
        return None

    async def submit(*, prompt, payload, model, api_key):
        return '```json\n{"severity": "green", "headline": "ok"}\n```\nbody'

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job_id = job.id

    factory = _factory(db_engine)
    await job_service.execute_hosted(factory, job_id=job_id, gcs_writer=writer, submit_override=submit)

    messages = [r.getMessage() for r in caplog.records if r.name == "bioaf.agent_review_job"]
    assert any("execute_hosted start" in m for m in messages), messages
    assert any("execute_hosted submitting" in m for m in messages), messages
    assert any("execute_hosted parsed" in m for m in messages), messages


@pytest.mark.asyncio
async def test_execute_hosted_logs_provider_error_with_detail(db_engine, admin_user, caplog):
    """A ProviderError during submit surfaces in the logs with its safe structured fields, never
    its message (which can carry a provider response body). Changed with owner approval
    2026-11-09: the raw-detail assertion became an exclusion."""
    import logging

    caplog.set_level(logging.WARNING, logger="bioaf.agent_review_job")

    async def writer(path, content):
        return None

    async def submit(*, prompt, payload, model, api_key):
        raise ProviderError(
            "SYNTH-PROVIDER-BODY dns lookup failed", error_class="transport", upstream_status=None, timeout=True
        )

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job_id = job.id

    factory = _factory(db_engine)
    await job_service.execute_hosted(factory, job_id=job_id, gcs_writer=writer, submit_override=submit)

    messages = [r.getMessage() for r in caplog.records if r.name == "bioaf.agent_review_job"]
    assert any("provider_error" in m and "error_class=transport" in m and "timeout=true" in m for m in messages), (
        messages
    )
    assert not any("SYNTH-PROVIDER-BODY" in m for m in messages), messages


@pytest.mark.asyncio
async def test_execute_hosted_unexpected_error_does_not_strand_job(db_engine, admin_user):
    """A non-ProviderError/non-ArtifactBuildError crash must still mark the job
    + review terminally failed, never leaving the job in an in-flight status
    (which would block every future review with a 409). Regression for the
    "Request failed" / nothing-happens bug."""

    async def writer(path, content):
        return None

    async def submit(*, prompt, payload, model, api_key):
        raise RuntimeError("kaboom: not a ProviderError")

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        job, review = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        job_id = job.id
        review_id = review.id

    factory = _factory(db_engine)
    # Must not raise out of the background task.
    await job_service.execute_hosted(factory, job_id=job_id, gcs_writer=writer, submit_override=submit)

    async with factory() as session:
        loaded_job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        loaded_review = (await session.execute(select(AgentReview).where(AgentReview.id == review_id))).scalar_one()
        assert loaded_job.status == "failed"
        assert loaded_job.status not in job_service.IN_FLIGHT_STATUSES
        assert loaded_review.status == "failed"

    # The debounce is released: a new review for the same run can be created.
    async with factory() as session:
        run_again = await _make_pipeline_run(session, admin_user.organization_id)
        job2, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_again,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        assert job2.id != job_id


@pytest.mark.asyncio
async def test_create_reaps_stale_in_flight_job(db_engine, admin_user):
    """A stranded in-flight job (worker died, never terminal) is reaped on the
    next create instead of blocking it with a 409 forever. Recovery for the
    'review_in_progress' lockout without needing an API restart."""
    from datetime import UTC, datetime, timedelta

    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        first, first_review = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        first_id = first.id
        first_review_id = first_review.id

    # Age the in-flight job well past the stale threshold.
    async with _factory(db_engine)() as session:
        job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == first_id))).scalar_one()
        job.created_at = datetime.now(UTC) - timedelta(minutes=20)
        job.submitted_at = None
        await session.commit()

    # The next create reaps it and succeeds rather than raising JobAlreadyRunning.
    async with _factory(db_engine)() as session:
        second, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        assert second.id != first_id

    async with _factory(db_engine)() as session:
        reaped = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == first_id))).scalar_one()
        assert reaped.status == "failed"
        reaped_review = (
            await session.execute(select(AgentReview).where(AgentReview.id == first_review_id))
        ).scalar_one()
        assert reaped_review.status == "failed"


@pytest.mark.asyncio
async def test_create_does_not_reap_recent_in_flight_job(db_engine, admin_user):
    """A recent in-flight job is a legitimate concurrent review and still wins
    the debounce (409), not reaped."""
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()

    async with _factory(db_engine)() as session:
        with pytest.raises(job_service.JobAlreadyRunning):
            await job_service.create(
                session,
                org_id=admin_user.organization_id,
                user_id=admin_user.id,
                entity_type="pipeline_run",
                entity_id=run_id,
                selected_sub_item_ids=["qc.metric_review"],
            )


@pytest.mark.asyncio
async def test_create_reaps_stale_gemma_zombie_job(db_engine, admin_user):
    """A stranded Gemma review job (dispatch stubbed -> sits in 'pending'
    forever, and mark_orphaned_on_startup skips Gemma) must still be reaped on
    create. Otherwise a legacy Gemma job permanently 409-blocks every review for
    the entity, surviving even a restart. Mirrors the demo's wedged job."""
    from datetime import UTC, datetime, timedelta

    from app.services.agent_review_prompt_builder import template_name_for_scope

    review_type = template_name_for_scope(False)
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id)
        run_id = await _make_pipeline_run(session, admin_user.organization_id)
        zombie = AgentReviewJob(
            organization_id=admin_user.organization_id,
            triggered_by_user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            review_type=review_type,
            provider="gemma",
            model="gemma-stub",
            prompt_template_version=review_type,
            status="pending",
            prompt_text="stub",
            created_at=datetime.now(UTC) - timedelta(minutes=30),
        )
        session.add(zombie)
        await session.commit()
        zombie_id = zombie.id

    async with _factory(db_engine)() as session:
        new_job, _ = await job_service.create(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            entity_type="pipeline_run",
            entity_id=run_id,
            selected_sub_item_ids=["qc.metric_review"],
        )
        await session.commit()
        assert new_job.id != zombie_id

    async with _factory(db_engine)() as session:
        reaped = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == zombie_id))).scalar_one()
        assert reaped.status == "failed"
