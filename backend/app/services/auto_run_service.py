"""Service for experiment auto-run configuration and pending run management.

Handles CRUD for auto-run configs, queuing pending runs on sample
completeness, cancelling on checksum mismatch, and launching pending
runs via the existing PipelineRunService.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.experiment_auto_run import ExperimentAutoRun
from app.models.pending_auto_run import PendingAutoRun
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.services.sample_completeness_service import check_sample_completeness
from app.services.vocabulary_validator import VocabularyValidator

logger = logging.getLogger("bioaf.auto_run_service")


class AutoRunService:
    # ---- CRUD ----

    @staticmethod
    async def create_config(
        session: AsyncSession,
        experiment_id: int,
        org_id: int,
        user_id: int,
        pipeline_key: str,
        parameters: dict | None = None,
        reference_genome: str | None = None,
        alignment_algorithm: str | None = None,
        delay_minutes: int = 0,
    ) -> ExperimentAutoRun:
        # Validate pipeline exists in catalog
        result = await session.execute(
            select(PipelineCatalogEntry).where(
                PipelineCatalogEntry.pipeline_key == pipeline_key,
                PipelineCatalogEntry.organization_id == org_id,
                PipelineCatalogEntry.enabled == True,  # noqa: E712
            )
        )
        if not result.scalar_one_or_none():
            raise ValueError(f"Pipeline '{pipeline_key}' not found in catalog")

        # Validate experiment exists
        exp_result = await session.execute(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.organization_id == org_id,
            )
        )
        if not exp_result.scalar_one_or_none():
            raise ValueError(f"Experiment {experiment_id} not found")

        # Validate CV fields
        await VocabularyValidator.validate_pipeline_run_fields(
            session,
            {"reference_genome": reference_genome, "alignment_algorithm": alignment_algorithm},
        )

        config = ExperimentAutoRun(
            organization_id=org_id,
            experiment_id=experiment_id,
            pipeline_key=pipeline_key,
            parameters_json=parameters or {},
            reference_genome=reference_genome,
            alignment_algorithm=alignment_algorithm,
            delay_minutes=delay_minutes,
            configured_by_user_id=user_id,
        )
        session.add(config)
        await session.flush()
        return config

    @staticmethod
    async def list_configs(
        session: AsyncSession,
        experiment_id: int,
        org_id: int,
    ) -> list[ExperimentAutoRun]:
        result = await session.execute(
            select(ExperimentAutoRun).where(
                ExperimentAutoRun.experiment_id == experiment_id,
                ExperimentAutoRun.organization_id == org_id,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_config(
        session: AsyncSession,
        config_id: int,
        org_id: int,
        updates: dict,
    ) -> ExperimentAutoRun | None:
        result = await session.execute(
            select(ExperimentAutoRun).where(
                ExperimentAutoRun.id == config_id,
                ExperimentAutoRun.organization_id == org_id,
            )
        )
        config = result.scalar_one_or_none()
        if not config:
            return None

        if "parameters" in updates and updates["parameters"] is not None:
            config.parameters_json = updates["parameters"]
        if "reference_genome" in updates:
            config.reference_genome = updates["reference_genome"]
        if "alignment_algorithm" in updates:
            config.alignment_algorithm = updates["alignment_algorithm"]
        if "delay_minutes" in updates and updates["delay_minutes"] is not None:
            config.delay_minutes = updates["delay_minutes"]
        if "enabled" in updates and updates["enabled"] is not None:
            config.enabled = updates["enabled"]

        await session.flush()
        return config

    @staticmethod
    async def delete_config(
        session: AsyncSession,
        config_id: int,
        org_id: int,
    ) -> bool:
        result = await session.execute(
            select(ExperimentAutoRun).where(
                ExperimentAutoRun.id == config_id,
                ExperimentAutoRun.organization_id == org_id,
            )
        )
        config = result.scalar_one_or_none()
        if not config:
            return False

        await session.delete(config)
        await session.flush()
        return True

    @staticmethod
    async def list_pending_runs(
        session: AsyncSession,
        config_id: int,
        org_id: int,
    ) -> list[PendingAutoRun]:
        result = await session.execute(
            select(PendingAutoRun).where(
                PendingAutoRun.auto_run_config_id == config_id,
                PendingAutoRun.organization_id == org_id,
            )
        )
        return list(result.scalars().all())

    # ---- Queuing and cancellation ----

    @staticmethod
    async def check_and_queue_auto_runs(
        session: AsyncSession,
        sample_id: int,
        sequencing_batch_id: int,
    ) -> None:
        """Check sample completeness and queue pending auto-runs if complete.

        Called from the ingest flow after a manifest entry is verified.
        """
        is_complete = await check_sample_completeness(session, sample_id, sequencing_batch_id)
        if not is_complete:
            return

        # Look up sample to find experiment
        from app.models.sample import Sample

        sample_result = await session.execute(select(Sample).where(Sample.id == sample_id))
        sample = sample_result.scalar_one_or_none()
        if not sample or not sample.experiment_id:
            return

        # Find enabled auto-run configs for this experiment
        configs_result = await session.execute(
            select(ExperimentAutoRun).where(
                ExperimentAutoRun.experiment_id == sample.experiment_id,
                ExperimentAutoRun.enabled == True,  # noqa: E712
            )
        )
        configs = list(configs_result.scalars().all())
        if not configs:
            return

        now = datetime.now(timezone.utc)

        for config in configs:
            # Check for existing pending run (idempotency)
            existing = await session.execute(
                select(PendingAutoRun).where(
                    PendingAutoRun.auto_run_config_id == config.id,
                    PendingAutoRun.sample_id == sample_id,
                )
            )
            if existing.scalar_one_or_none():
                continue

            pending = PendingAutoRun(
                organization_id=config.organization_id,
                auto_run_config_id=config.id,
                experiment_id=config.experiment_id,
                sample_id=sample_id,
                sample_completed_at=now,
                scheduled_at=now + timedelta(minutes=config.delay_minutes),
                status="waiting",
            )
            session.add(pending)

        await session.flush()

    @staticmethod
    async def cancel_pending_runs_for_sample(
        session: AsyncSession,
        sample_id: int,
        reason: str,
    ) -> int:
        """Cancel all waiting pending runs for a sample.

        Called from the ingest flow when a checksum mismatch is detected.
        Returns the number of cancelled runs.
        """
        result = await session.execute(
            update(PendingAutoRun)
            .where(
                PendingAutoRun.sample_id == sample_id,
                PendingAutoRun.status == "waiting",
            )
            .values(status="cancelled", cancelled_reason=reason)
        )
        await session.flush()
        return result.rowcount  # type: ignore[return-value]

    # ---- Launch loop ----

    @staticmethod
    async def process_pending_runs(session: AsyncSession) -> int:
        """Process all due pending runs. Called by the background loop.

        Returns the number of runs processed (launched or cancelled).
        """
        from decimal import Decimal

        from app.models.budget_config import BudgetConfig
        from app.schemas.pipeline_run import PipelineRunLaunchRequest
        from app.services.cost_service import CostService
        from app.services.event_bus import event_bus
        from app.services.event_types import AUTO_RUN_BUDGET_DISABLED, AUTO_RUN_LAUNCHED
        from app.services.pipeline_run_service import PipelineRunService

        now = datetime.now(timezone.utc)

        # Fetch all due pending runs
        result = await session.execute(
            select(PendingAutoRun)
            .where(
                PendingAutoRun.status == "waiting",
                PendingAutoRun.scheduled_at <= now,
            )
            .order_by(PendingAutoRun.scheduled_at)
        )
        pending_runs = list(result.scalars().all())
        if not pending_runs:
            return 0

        # Group by org for budget checks
        by_org: dict[int, list[PendingAutoRun]] = {}
        for pr in pending_runs:
            by_org.setdefault(pr.organization_id, []).append(pr)

        processed = 0

        for org_id, org_runs in by_org.items():
            # Budget check
            budget_result = await session.execute(select(BudgetConfig).where(BudgetConfig.organization_id == org_id))
            budget_config = budget_result.scalar_one_or_none()

            if budget_config and budget_config.monthly_budget:
                summary = await CostService.get_cost_summary(session, org_id)
                current_spend = Decimal(str(summary.get("current_month_spend", 0)))
                threshold = budget_config.monthly_budget * Decimal("0.90")

                if current_spend >= threshold:
                    # Cancel all waiting runs for this org
                    await session.execute(
                        update(PendingAutoRun)
                        .where(
                            PendingAutoRun.organization_id == org_id,
                            PendingAutoRun.status == "waiting",
                        )
                        .values(status="cancelled", cancelled_reason="budget_limit")
                    )
                    # Disable all auto-run configs for this org
                    await session.execute(
                        update(ExperimentAutoRun)
                        .where(
                            ExperimentAutoRun.organization_id == org_id,
                            ExperimentAutoRun.enabled == True,  # noqa: E712
                        )
                        .values(enabled=False)
                    )
                    await session.flush()

                    await event_bus.emit(
                        AUTO_RUN_BUDGET_DISABLED,
                        {
                            "event_type": AUTO_RUN_BUDGET_DISABLED,
                            "org_id": org_id,
                            "entity_type": "auto_run",
                            "title": "Auto-run disabled due to budget limit",
                            "message": (
                                f"Spend ${float(current_spend):.2f} reached 90% of "
                                f"${float(budget_config.monthly_budget):.2f} budget. "
                                f"{len(org_runs)} pending run(s) cancelled."
                            ),
                            "severity": "critical",
                            "summary": f"Auto-run disabled: budget limit reached ({len(org_runs)} runs cancelled)",
                            "metadata": {
                                "current_spend": float(current_spend),
                                "monthly_budget": float(budget_config.monthly_budget),
                                "cancelled_count": len(org_runs),
                            },
                        },
                    )
                    processed += len(org_runs)
                    continue

            # Launch each pending run
            for pr in org_runs:
                config_result = await session.execute(
                    select(ExperimentAutoRun).where(ExperimentAutoRun.id == pr.auto_run_config_id)
                )
                config = config_result.scalar_one_or_none()
                if not config or not config.enabled:
                    pr.status = "cancelled"
                    pr.cancelled_reason = "config_disabled"
                    processed += 1
                    continue

                try:
                    # Build a launch request identical to manual launch
                    launch_data = PipelineRunLaunchRequest(
                        pipeline_key=config.pipeline_key,
                        experiment_id=config.experiment_id,
                        sample_ids=[pr.sample_id],
                        parameters=config.parameters_json or {},
                        reference_genome=config.reference_genome,
                        alignment_algorithm=config.alignment_algorithm,
                    )
                    run = await PipelineRunService.launch_run(
                        session,
                        config.organization_id,
                        config.configured_by_user_id,
                        launch_data,
                    )
                    pr.status = "launched"
                    pr.pipeline_run_id = run.id
                    await session.flush()

                    await event_bus.emit(
                        AUTO_RUN_LAUNCHED,
                        {
                            "event_type": AUTO_RUN_LAUNCHED,
                            "org_id": config.organization_id,
                            "user_id": config.configured_by_user_id,
                            "entity_type": "pipeline_run",
                            "entity_id": run.id,
                            "title": f"Auto-run launched: {config.pipeline_key}",
                            "message": (
                                f"Pipeline '{config.pipeline_key}' auto-launched for experiment {config.experiment_id}"
                            ),
                            "summary": f"Auto-run launched pipeline '{config.pipeline_key}' (run {run.id})",
                            "metadata": {
                                "pipeline_run_id": run.id,
                                "sample_id": pr.sample_id,
                                "experiment_id": config.experiment_id,
                                "pipeline_key": config.pipeline_key,
                            },
                        },
                    )
                except Exception as exc:
                    logger.exception(
                        "Auto-run launch failed for pending_auto_run %d: %s",
                        pr.id,
                        exc,
                    )
                    pr.status = "cancelled"
                    pr.cancelled_reason = str(exc)[:500]

                processed += 1

        await session.flush()
        return processed
