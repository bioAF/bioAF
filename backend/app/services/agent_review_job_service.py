"""Agent review job dispatch and lifecycle (ADR-055, spec-llm-integration-jobs).

One service module wraps both execution paths (hosted HTTP and Gemma pipeline)
behind a uniform `create` + `execute_hosted` interface. Debounce is enforced
by the partial unique index on agent_review_jobs at the DB layer; the service
catches the unique-violation and raises a typed error so the endpoint can
return 409 with the existing job id.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_review import AgentReview
from app.models.agent_review_job import AgentReviewJob
from app.models.llm_provider_config import LlmProviderConfig
from app.services import audit_service, llm_provider_config_service
from app.services.agent_review_artifact_builder import (
    ArtifactBuildError,
    build_for_run,
    render_experiment_header,
)
from app.services.agent_review_prompt_builder import (
    EmptySectionSelection,
    assemble_prompt,
    template_name_for_scope,
)
from app.services.agent_review_prompts import (
    EXPERIMENT_RUN_COMPARISON_V1_NAME,
    PIPELINE_RUN_REVIEW_V1_NAME,
    get_template,
)
from app.services.agent_review_response_parser import parse as parse_response
from app.services.llm_provider_clients import ProviderError, get_client

logger = logging.getLogger("bioaf.agent_review_job")

VALID_ENTITY_TYPES = {"pipeline_run", "experiment"}
VALID_REVIEW_TYPES = {PIPELINE_RUN_REVIEW_V1_NAME, EXPERIMENT_RUN_COMPARISON_V1_NAME}

IN_FLIGHT_STATUSES = ("pending", "building_artifacts", "submitted")


class JobAlreadyRunning(Exception):
    """Raised when the debounce partial-unique-index trips on create."""

    def __init__(self, existing_job_id: int, existing_agent_review_id: int | None) -> None:
        super().__init__(f"review already in progress (job_id={existing_job_id})")
        self.existing_job_id = existing_job_id
        self.existing_agent_review_id = existing_agent_review_id


class NoActiveProvider(Exception):
    """Raised when an org has no active LLM provider configured."""


async def _get_inflight(
    session: AsyncSession, entity_type: str, entity_id: int, review_type: str
) -> AgentReviewJob | None:
    result = await session.execute(
        select(AgentReviewJob).where(
            AgentReviewJob.entity_type == entity_type,
            AgentReviewJob.entity_id == entity_id,
            AgentReviewJob.review_type == review_type,
            AgentReviewJob.status.in_(IN_FLIGHT_STATUSES),
        )
    )
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    entity_type: str,
    entity_id: int,
    review_type: str | None = None,
    included_run_ids: list[int] | None = None,
    include_html_report_run_ids: list[int] | None = None,
    selected_sub_item_ids: list[str] | None = None,
    custom_prompt_id: int | None = None,
    custom_prompt_body: str | None = None,
) -> tuple[AgentReviewJob, AgentReview]:
    """Snapshot the active provider and create both rows atomically.

    Prompt assembly is one of three modes (mutually exclusive, checked in order):
    - custom_prompt_id: use the saved AgentReviewPrompt body verbatim.
    - custom_prompt_body: use the one-off body verbatim (not persisted to a saved row).
    - selected_sub_item_ids: assemble from the section catalog.

    review_type is auto-derived from entity_type when omitted (the legacy v1
    template names are retained for back-compat with any caller still passing
    them explicitly).
    """
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"invalid entity_type: {entity_type}")

    experiment_scope = entity_type == "experiment"
    builder_template = template_name_for_scope(experiment_scope)

    # Resolve prompt source.
    prompt_source: str
    prompt_text: str
    prompt_sections: list[str] | None = None
    prompt_custom_id: int | None = None

    if custom_prompt_id is not None:
        from app.services import agent_review_prompt_service

        saved = await agent_review_prompt_service.get_for_org(session, org_id, custom_prompt_id)
        if saved is None:
            raise ValueError(f"saved prompt {custom_prompt_id} not found in this org")
        prompt_source = "custom_saved"
        prompt_text = saved.body
        prompt_custom_id = saved.id
    elif custom_prompt_body is not None:
        if not custom_prompt_body.strip():
            raise ValueError("custom_prompt_body must not be empty")
        prompt_source = "custom_one_off"
        prompt_text = custom_prompt_body
    else:
        if not selected_sub_item_ids:
            raise EmptySectionSelection("no sub-items selected for the prompt builder")
        prompt_source = "builder"
        prompt_text = assemble_prompt(
            experiment_scope=experiment_scope,
            selected_sub_item_ids=selected_sub_item_ids,
        )
        prompt_sections = list(selected_sub_item_ids)

    resolved_review_type = review_type or builder_template
    # The legacy v1 templates remain valid review_type values, but anything
    # else must match the builder template name.
    if resolved_review_type not in VALID_REVIEW_TYPES and resolved_review_type != builder_template:
        raise ValueError(f"invalid review_type: {resolved_review_type}")

    active = await llm_provider_config_service.get_active(session, org_id)
    if active is None:
        raise NoActiveProvider("no active LLM provider configured for this org")
    if not active.model:
        raise NoActiveProvider("active provider has no model configured")

    job = AgentReviewJob(
        organization_id=org_id,
        triggered_by_user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        review_type=resolved_review_type,
        provider=active.provider,
        model=active.model,
        prompt_template_version=resolved_review_type,
        status="pending",
        included_run_ids=included_run_ids,
        include_html_report_run_ids=include_html_report_run_ids,
        prompt_text=prompt_text,
        prompt_sections=prompt_sections,
        prompt_source=prompt_source,
        prompt_custom_id=prompt_custom_id,
    )
    session.add(job)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # Re-query in a fresh transaction to find the existing in-flight job.
        existing = await _get_inflight(session, entity_type, entity_id, resolved_review_type)
        existing_review_id = existing.agent_review_id if existing else None
        raise JobAlreadyRunning(
            existing_job_id=existing.id if existing else -1,
            existing_agent_review_id=existing_review_id,
        ) from exc

    review = AgentReview(
        organization_id=org_id,
        triggered_by_user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        included_run_ids=included_run_ids,
        review_type=resolved_review_type,
        provider=active.provider,
        model=active.model,
        prompt_template_version=resolved_review_type,
        status="pending",
        agent_review_job_id=job.id,
        prompt_text=prompt_text,
        prompt_sections=prompt_sections,
        prompt_source=prompt_source,
        prompt_custom_id=prompt_custom_id,
    )
    session.add(review)
    await session.flush()
    job.agent_review_id = review.id
    await session.flush()
    return job, review


async def _write_review_terminal(
    session: AsyncSession,
    review_id: int,
    *,
    status: str,
    severity: str | None,
    headline: str | None,
    flags: list[dict] | None,
    evidence: list[str] | None,
    body: str | None,
    error_text: str | None,
    artifact_gcs_paths: list[str],
) -> None:
    await session.execute(
        update(AgentReview)
        .where(AgentReview.id == review_id)
        .values(
            status=status,
            severity=severity,
            headline=headline,
            flags=flags,
            evidence=evidence,
            body=body,
            error_text=error_text,
            artifact_gcs_paths=artifact_gcs_paths,
            completed_at=datetime.now(UTC),
        )
    )


async def _write_job_terminal(
    session: AsyncSession,
    job_id: int,
    *,
    status: str,
    error_text: str | None,
    error_class: str | None,
    artifact_gcs_paths: list[str] | None = None,
) -> None:
    values = {
        "status": status,
        "completed_at": datetime.now(UTC),
        "error_text": error_text,
        "error_class": error_class,
    }
    if artifact_gcs_paths is not None:
        values["artifact_gcs_paths"] = artifact_gcs_paths
    await session.execute(update(AgentReviewJob).where(AgentReviewJob.id == job_id).values(**values))


def _audit_details_submitted(job: AgentReviewJob, key_prefix_last5: str | None) -> dict:
    return {
        "agent_review_job_id": job.id,
        "agent_review_id": job.agent_review_id,
        "entity_type": job.entity_type,
        "entity_id": job.entity_id,
        "review_type": job.review_type,
        "provider": job.provider,
        "model": job.model,
        "prompt_template_version": job.prompt_template_version,
        "api_key_prefix_last5": key_prefix_last5,
        "included_run_ids": job.included_run_ids,
        "artifact_gcs_paths": list(job.artifact_gcs_paths or []),
    }


async def execute_hosted(
    session_factory,
    *,
    job_id: int,
    gcs_writer=None,
    qc_report_provider=None,
    submit_override=None,
) -> None:
    """Run a hosted LLM review job to completion.

    Owns its own DB session (so it can run as a FastAPI BackgroundTasks task
    outside the request lifespan). Tests inject a session_factory bound to the
    test engine, a gcs_writer recorder, an optional qc_report_provider hook,
    and a submit_override so they do not have to mock the HTTP client.
    """
    async with session_factory() as session:
        job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        provider_cfg = (
            await session.execute(
                select(LlmProviderConfig).where(
                    LlmProviderConfig.organization_id == job.organization_id,
                    LlmProviderConfig.provider == job.provider,
                )
            )
        ).scalar_one()

        logger.info(
            "execute_hosted start: job_id=%d provider=%s model=%s entity=%s/%d review_id=%s",
            job.id,
            job.provider,
            job.model,
            job.entity_type,
            job.entity_id,
            job.agent_review_id,
        )

        # Move to building_artifacts. No audit row yet; the contract is
        # llm_review_submitted is written once the artifact has been built and
        # the call has been made. A pre-submission failure is reported as
        # llm_review_failed without a corresponding submitted row.
        job.status = "building_artifacts"
        await session.flush()

        try:
            built_paths: list[str] = []
            assembled_payload_chunks: list[str] = []

            if job.entity_type == "pipeline_run":
                qc = await qc_report_provider(job.entity_id) if qc_report_provider else None
                artifact = await build_for_run(
                    session,
                    run_id=job.entity_id,
                    qc_report_content=qc,
                    gcs_writer=gcs_writer,
                    job_id=job.id,
                )
                built_paths.append(artifact.gcs_path)
                assembled_payload_chunks.append(artifact.markdown)
            else:
                # entity_type == 'experiment'
                from app.models.experiment import Experiment

                exp = (await session.execute(select(Experiment).where(Experiment.id == job.entity_id))).scalar_one()
                run_ids = list(job.included_run_ids or [])
                header = render_experiment_header(
                    experiment_id=exp.id,
                    experiment_name=exp.name,
                    experiment_status=exp.status,
                    included_run_ids=run_ids,
                )
                assembled_payload_chunks.append(header)
                for rid in run_ids:
                    qc = await qc_report_provider(rid) if qc_report_provider else None
                    art = await build_for_run(
                        session,
                        run_id=rid,
                        qc_report_content=qc,
                        gcs_writer=gcs_writer,
                        job_id=job.id,
                    )
                    built_paths.append(art.gcs_path)
                    assembled_payload_chunks.append(art.markdown)

            job.artifact_gcs_paths = built_paths
            await session.flush()
            logger.info(
                "execute_hosted artifacts built: job_id=%d count=%d paths=%s",
                job.id,
                len(built_paths),
                built_paths,
            )
        except ArtifactBuildError as exc:
            logger.exception("execute_hosted artifact build failed: job_id=%d", job.id)
            await _write_job_terminal(
                session,
                job.id,
                status="failed",
                error_text=str(exc),
                error_class="artifact_build_failure",
            )
            await _write_review_terminal(
                session,
                job.agent_review_id,
                status="failed",
                severity=None,
                headline="Artifact build failed",
                flags=None,
                evidence=None,
                body=None,
                error_text=str(exc),
                artifact_gcs_paths=[],
            )
            await audit_service.log_action(
                session,
                user_id=job.triggered_by_user_id,
                entity_type="agent_review_job",
                entity_id=job.id,
                action="llm_review_failed",
                details={
                    "agent_review_job_id": job.id,
                    "agent_review_id": job.agent_review_id,
                    "error_class": "artifact_build_failure",
                    "error_text": str(exc)[:4000],
                },
            )
            await session.commit()
            return

        # Transition to submitted; write audit row at the boundary where data
        # actually leaves the org.
        job.status = "submitted"
        job.submitted_at = datetime.now(UTC)
        await session.flush()
        await audit_service.log_action(
            session,
            user_id=job.triggered_by_user_id,
            entity_type="agent_review_job",
            entity_id=job.id,
            action="llm_review_submitted",
            details=_audit_details_submitted(job, provider_cfg.api_key_prefix_last5),
        )
        await session.commit()

    # Network call happens outside any open transaction.
    # prompt_text is the snapshot written by create(); fall back to the legacy
    # versioned template for any historical job that predates the section
    # builder columns (those will be null on disk).
    prompt = job.prompt_text or get_template(job.review_type)
    payload = "\n\n".join(assembled_payload_chunks)
    submit_callable = submit_override if submit_override is not None else get_client(job.provider).submit
    logger.info(
        "execute_hosted submitting: job_id=%d provider=%s model=%s prompt_chars=%d payload_chars=%d",
        job.id,
        job.provider,
        job.model,
        len(prompt),
        len(payload),
    )
    try:
        response_text = await submit_callable(
            prompt=prompt,
            payload=payload,
            model=job.model,
            api_key=provider_cfg.api_key,
        )
    except ProviderError as exc:
        logger.warning(
            "execute_hosted provider_error: job_id=%d error_class=%s detail=%s",
            job.id,
            exc.error_class,
            str(exc) or repr(exc),
        )
        err_text = str(exc).strip() or f"{exc.error_class} (no provider detail)"
        async with session_factory() as session:
            job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
            await _write_job_terminal(
                session,
                job.id,
                status="failed",
                error_text=err_text,
                error_class="provider_error",
            )
            await _write_review_terminal(
                session,
                job.agent_review_id,
                status="failed",
                severity=None,
                headline=f"Provider error ({exc.error_class})",
                flags=None,
                evidence=None,
                body=None,
                error_text=err_text,
                artifact_gcs_paths=built_paths,
            )
            await audit_service.log_action(
                session,
                user_id=job.triggered_by_user_id,
                entity_type="agent_review_job",
                entity_id=job.id,
                action="llm_review_failed",
                details={
                    "agent_review_job_id": job.id,
                    "agent_review_id": job.agent_review_id,
                    "error_class": "provider_error",
                    "provider_error_class": exc.error_class,
                    "error_text": err_text[:4000],
                },
            )
            await session.commit()
        return

    # Parse and persist success.
    parsed = parse_response(response_text)
    logger.info(
        "execute_hosted parsed: job_id=%d severity=%s parse_failure=%s response_chars=%d",
        job_id,
        parsed.severity,
        parsed.parse_failure,
        len(response_text or ""),
    )
    async with session_factory() as session:
        job = (await session.execute(select(AgentReviewJob).where(AgentReviewJob.id == job_id))).scalar_one()
        await _write_review_terminal(
            session,
            job.agent_review_id,
            status="succeeded",
            severity=parsed.severity,
            headline=parsed.headline,
            flags=parsed.flags,
            evidence=parsed.evidence,
            body=parsed.body or response_text,
            error_text=None,
            artifact_gcs_paths=built_paths,
        )
        await _write_job_terminal(
            session,
            job.id,
            status="succeeded",
            error_text=None,
            error_class=None,
        )
        await audit_service.log_action(
            session,
            user_id=job.triggered_by_user_id,
            entity_type="agent_review_job",
            entity_id=job.id,
            action="llm_review_succeeded",
            details={
                "agent_review_job_id": job.id,
                "agent_review_id": job.agent_review_id,
                "severity": parsed.severity,
                "parse_failure": parsed.parse_failure,
            },
        )
        await session.commit()


async def mark_orphaned_on_startup(session: AsyncSession) -> int:
    """Transition any in-flight hosted-path jobs to failed with reason process_restart.

    Gemma jobs are not touched: the orchestrator owns them and will resolve
    them when the underlying pipeline run reaches a terminal state.
    """
    result = await session.execute(
        select(AgentReviewJob).where(
            AgentReviewJob.status.in_(IN_FLIGHT_STATUSES),
            AgentReviewJob.provider != "gemma",
        )
    )
    affected = list(result.scalars().all())
    for job in affected:
        await _write_job_terminal(
            session,
            job.id,
            status="failed",
            error_text="API process restarted while job was in flight.",
            error_class="process_restart",
        )
        await _write_review_terminal(
            session,
            job.agent_review_id,
            status="failed",
            severity=None,
            headline="API process restarted",
            flags=None,
            evidence=None,
            body=None,
            error_text="API process restarted while job was in flight.",
            artifact_gcs_paths=list(job.artifact_gcs_paths or []),
        )
        await audit_service.log_action(
            session,
            user_id=job.triggered_by_user_id,
            entity_type="agent_review_job",
            entity_id=job.id,
            action="llm_review_failed",
            details={
                "agent_review_job_id": job.id,
                "agent_review_id": job.agent_review_id,
                "error_class": "process_restart",
                "error_text": "API process restarted while job was in flight.",
            },
        )
    await session.flush()
    return len(affected)
