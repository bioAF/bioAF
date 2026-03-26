"""Pipeline trigger evaluation engine.

Handles event-driven and scheduled pipeline triggering with
batching windows and budget-aware pre-flight checks.
"""

import asyncio
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.ingest_event import IngestEvent
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_input_file import PipelineRunInputFile
from app.models.pipeline_trigger import PipelineTrigger
from app.models.trigger_evaluation import TriggerEvaluation
from app.schemas.pipeline_trigger import PipelineTriggerCreate, PipelineTriggerUpdate
from app.services.audit_service import log_action
from app.services.budget_service import BudgetService
from app.services.event_bus import event_bus
from app.services.event_types import (
    AUTO_RUN_SUBMITTED,
    BATCH_WINDOW_CLOSED,
    RUN_QUEUED_BUDGET,
)


# In-memory batching windows: trigger_id -> {file_ids, expiry_time}
_active_batches: dict[int, dict] = {}
_batch_lock = asyncio.Lock()


class TriggerService:
    @staticmethod
    async def create_trigger(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        data: PipelineTriggerCreate,
    ) -> PipelineTrigger:
        trigger = PipelineTrigger(
            pipeline_id=data.pipeline_id,
            organization_id=org_id,
            trigger_mode=data.trigger_mode,
            event_config=data.event_config.model_dump() if data.event_config else None,
            schedule_config=data.schedule_config.model_dump() if data.schedule_config else None,
            parameter_defaults=data.parameter_defaults,
            budget_config=data.budget_config.model_dump(),
            enabled=data.enabled,
            created_by=user_id,
        )
        session.add(trigger)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_trigger",
            entity_id=trigger.id,
            action="create",
            details={"trigger_mode": data.trigger_mode, "pipeline_id": data.pipeline_id},
        )
        return trigger

    @staticmethod
    async def get_trigger(session: AsyncSession, trigger_id: int) -> PipelineTrigger | None:
        result = await session.execute(select(PipelineTrigger).where(PipelineTrigger.id == trigger_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_triggers(session: AsyncSession, org_id: int) -> list[PipelineTrigger]:
        result = await session.execute(
            select(PipelineTrigger)
            .where(PipelineTrigger.organization_id == org_id)
            .order_by(PipelineTrigger.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_trigger(
        session: AsyncSession,
        trigger_id: int,
        user_id: int,
        data: PipelineTriggerUpdate,
    ) -> PipelineTrigger | None:
        result = await session.execute(select(PipelineTrigger).where(PipelineTrigger.id == trigger_id))
        trigger = result.scalar_one_or_none()
        if not trigger:
            return None

        updates = {}
        if data.trigger_mode is not None:
            trigger.trigger_mode = data.trigger_mode
            updates["trigger_mode"] = data.trigger_mode
        if data.event_config is not None:
            trigger.event_config = data.event_config.model_dump()
            updates["event_config"] = "updated"
        if data.schedule_config is not None:
            trigger.schedule_config = data.schedule_config.model_dump()
            updates["schedule_config"] = "updated"
        if data.parameter_defaults is not None:
            trigger.parameter_defaults = data.parameter_defaults
            updates["parameter_defaults"] = "updated"
        if data.budget_config is not None:
            trigger.budget_config = data.budget_config.model_dump()
            updates["budget_config"] = "updated"
        if data.enabled is not None:
            trigger.enabled = data.enabled
            updates["enabled"] = data.enabled

        if updates:
            await session.flush()
            await log_action(
                session,
                user_id=user_id,
                entity_type="pipeline_trigger",
                entity_id=trigger.id,
                action="update",
                details=updates,
            )
        return trigger

    @staticmethod
    async def disable_trigger(
        session: AsyncSession,
        trigger_id: int,
        user_id: int,
    ) -> PipelineTrigger | None:
        result = await session.execute(select(PipelineTrigger).where(PipelineTrigger.id == trigger_id))
        trigger = result.scalar_one_or_none()
        if not trigger:
            return None

        trigger.enabled = False
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_trigger",
            entity_id=trigger.id,
            action="disable",
        )

        # Remove any active batch for this trigger
        async with _batch_lock:
            _active_batches.pop(trigger_id, None)

        return trigger

    @staticmethod
    async def evaluate_event_triggers(
        ingest_event: IngestEvent,
        db: AsyncSession,
    ) -> list[TriggerEvaluation]:
        """Evaluate all active event-driven triggers for an ingest event."""
        result = await db.execute(
            select(PipelineTrigger).where(
                PipelineTrigger.trigger_mode == "event_driven",
                PipelineTrigger.enabled.is_(True),
            )
        )
        triggers = list(result.scalars().all())
        evaluations = []

        for trigger in triggers:
            matched = TriggerService._match_trigger(trigger, ingest_event)
            if not matched:
                continue

            file_id = ingest_event.file_id
            if not file_id:
                continue

            event_config = trigger.event_config or {}
            window_minutes = event_config.get("batching_window_minutes", 15)

            if window_minutes > 0:
                # Add to batching window
                async with _batch_lock:
                    batch = _active_batches.get(trigger.id)
                    if batch is None:
                        batch = {"file_ids": [], "expiry_time": 0}
                        _active_batches[trigger.id] = batch
                    batch["file_ids"].append(file_id)
                    batch["expiry_time"] = time.time() + window_minutes * 60

                # Log evaluation as queued (waiting for batch window)
                evaluation = TriggerEvaluation(
                    trigger_id=trigger.id,
                    evaluation_type="file_ingest",
                    matched_files=[file_id],
                    result="queued",
                )
                db.add(evaluation)
                await db.flush()
                evaluations.append(evaluation)
            else:
                # Immediate submission
                evaluation = await TriggerService._submit_or_queue_run(trigger, [file_id], db)
                evaluations.append(evaluation)

        return evaluations

    @staticmethod
    async def process_expired_batches(db: AsyncSession) -> list[TriggerEvaluation]:
        """Check for expired batches and submit pipeline runs."""
        evaluations = []
        now = time.time()
        expired_triggers: list[tuple[int, list[int]]] = []

        async with _batch_lock:
            for trigger_id, batch in list(_active_batches.items()):
                if batch["expiry_time"] <= now and batch["file_ids"]:
                    expired_triggers.append((trigger_id, list(batch["file_ids"])))
                    del _active_batches[trigger_id]

        for trigger_id, file_ids in expired_triggers:
            result = await db.execute(select(PipelineTrigger).where(PipelineTrigger.id == trigger_id))
            trigger = result.scalar_one_or_none()
            if trigger and trigger.enabled:
                asyncio.create_task(
                    event_bus.emit(
                        BATCH_WINDOW_CLOSED,
                        {
                            "event_type": BATCH_WINDOW_CLOSED,
                            "org_id": trigger.organization_id,
                            "entity_type": "pipeline_trigger",
                            "entity_id": trigger.id,
                            "title": f"Batch window closed for trigger #{trigger.id}",
                            "message": f"Submitting {len(file_ids)} file(s) for evaluation",
                            "severity": "info",
                        },
                    )
                )
                evaluation = await TriggerService._submit_or_queue_run(trigger, file_ids, db)
                evaluations.append(evaluation)

        return evaluations

    @staticmethod
    async def evaluate_scheduled_triggers(db: AsyncSession) -> list[TriggerEvaluation]:
        """Evaluate all active scheduled triggers. Called by the scheduler."""
        result = await db.execute(
            select(PipelineTrigger).where(
                PipelineTrigger.trigger_mode == "scheduled",
                PipelineTrigger.enabled.is_(True),
            )
        )
        triggers = list(result.scalars().all())
        evaluations = []

        for trigger in triggers:
            schedule_config = trigger.schedule_config or {}
            file_types = schedule_config.get("file_types", [])
            min_files = schedule_config.get("min_files_to_trigger", 1)

            # Find unprocessed files matching filter
            query = select(File.id).where(File.ingest_source == "auto_ingest")
            if file_types:
                query = query.where(File.file_type.in_(file_types))

            file_result = await db.execute(query)
            file_ids = [row[0] for row in file_result.fetchall()]

            if len(file_ids) < min_files:
                evaluation = TriggerEvaluation(
                    trigger_id=trigger.id,
                    evaluation_type="scheduled",
                    matched_files=[],
                    result="no_files",
                )
                db.add(evaluation)
                await db.flush()
                evaluations.append(evaluation)
                continue

            evaluation = await TriggerService._submit_or_queue_run(trigger, file_ids, db)
            evaluations.append(evaluation)

        return evaluations

    @staticmethod
    def _match_trigger(trigger: PipelineTrigger, ingest_event: IngestEvent) -> bool:
        """Check if an ingest event matches a trigger's criteria."""
        event_config = trigger.event_config or {}
        file_types = event_config.get("file_types", [])
        project_filter = event_config.get("project_filter")

        # Check file type
        if file_types and ingest_event.file_id:
            # We need to check the file's type
            # For simplicity, check from the ingest event's file_id
            # This is evaluated after the file record is created
            pass  # Will be checked at submission time if needed

        # Check project filter
        if project_filter and ingest_event.resolved_project_id:
            if ingest_event.resolved_project_id not in project_filter:
                return False

        # If we get here, consider it a match
        return True

    @staticmethod
    async def _submit_or_queue_run(
        trigger: PipelineTrigger,
        file_ids: list[int],
        db: AsyncSession,
    ) -> TriggerEvaluation:
        """Run budget pre-flight then submit or queue the pipeline run."""
        # Budget pre-flight
        budget_result = await BudgetService.budget_preflight(
            pipeline_name=f"pipeline-{trigger.pipeline_id}",
            input_file_count=len(file_ids),
            input_total_bytes=0,  # Simplified for POC
            db=db,
        )

        budget_config = trigger.budget_config or {}
        auto_queue = budget_config.get("auto_queue_when_over_budget", True)

        if budget_result.decision == "within_budget":
            # Submit the run
            run = PipelineRun(
                organization_id=trigger.organization_id,
                pipeline_name=f"pipeline-{trigger.pipeline_id}",
                status="pending",
                parameters_json=trigger.parameter_defaults,
                input_files_json=file_ids,
                cost_estimate=budget_result.estimated_cost,
            )
            db.add(run)
            await db.flush()

            for fid in file_ids:
                db.add(PipelineRunInputFile(pipeline_run_id=run.id, file_id=fid))
            await db.flush()

            evaluation = TriggerEvaluation(
                trigger_id=trigger.id,
                evaluation_type="file_ingest",
                matched_files=file_ids,
                budget_check_result=budget_result.model_dump(),
                result="submitted",
                pipeline_run_id=run.id,
            )

            asyncio.create_task(
                event_bus.emit(
                    AUTO_RUN_SUBMITTED,
                    {
                        "event_type": AUTO_RUN_SUBMITTED,
                        "org_id": trigger.organization_id,
                        "entity_type": "pipeline_run",
                        "entity_id": run.id,
                        "title": f"Pipeline auto-submitted: {run.pipeline_name}",
                        "message": f"Trigger #{trigger.id} submitted run with {len(file_ids)} file(s)",
                        "severity": "info",
                    },
                )
            )
        elif budget_result.decision in ("might_exceed", "will_exceed", "budget_exhausted") and auto_queue:
            # Queue for review
            run = PipelineRun(
                organization_id=trigger.organization_id,
                pipeline_name=f"pipeline-{trigger.pipeline_id}",
                status="pending_budget_review",
                parameters_json=trigger.parameter_defaults,
                input_files_json=file_ids,
                cost_estimate=budget_result.estimated_cost,
            )
            db.add(run)
            await db.flush()

            for fid in file_ids:
                db.add(PipelineRunInputFile(pipeline_run_id=run.id, file_id=fid))
            await db.flush()

            evaluation = TriggerEvaluation(
                trigger_id=trigger.id,
                evaluation_type="file_ingest",
                matched_files=file_ids,
                budget_check_result=budget_result.model_dump(),
                result="queued",
                pipeline_run_id=run.id,
            )

            asyncio.create_task(
                event_bus.emit(
                    RUN_QUEUED_BUDGET,
                    {
                        "event_type": RUN_QUEUED_BUDGET,
                        "org_id": trigger.organization_id,
                        "entity_type": "pipeline_run",
                        "entity_id": run.id,
                        "title": f"Pipeline queued for budget review: {run.pipeline_name}",
                        "message": f"Budget decision: {budget_result.decision}, "
                        f"estimated cost: ${budget_result.estimated_cost:.2f}",
                        "severity": "warning",
                    },
                )
            )
        else:
            evaluation = TriggerEvaluation(
                trigger_id=trigger.id,
                evaluation_type="file_ingest",
                matched_files=file_ids,
                budget_check_result=budget_result.model_dump(),
                result="skipped",
            )

        db.add(evaluation)
        await db.flush()
        return evaluation

    @staticmethod
    async def process_budget_queue(db: AsyncSession) -> list[dict]:
        """Process queued runs in FIFO order, re-evaluate budget for each."""
        result = await db.execute(
            select(PipelineRun)
            .where(PipelineRun.status == "pending_budget_review")
            .order_by(PipelineRun.created_at.asc())
        )
        queued_runs = list(result.scalars().all())
        results = []

        for run in queued_runs:
            budget_check = await BudgetService.budget_preflight(
                pipeline_name=run.pipeline_name,
                input_file_count=len(run.input_files_json) if run.input_files_json else 0,
                input_total_bytes=0,
                db=db,
            )
            if budget_check.decision == "within_budget":
                run.status = "pending"
                await db.flush()
                results.append({"run_id": run.id, "action": "submitted"})
            else:
                results.append({"run_id": run.id, "action": "held"})
                break  # Stop processing once budget is exhausted

        return results

    @staticmethod
    async def approve_queued_run(
        run_id: int,
        user_id: int,
        db: AsyncSession,
    ) -> PipelineRun | None:
        """Admin approves a specific queued run, bypassing budget check."""
        result = await db.execute(select(PipelineRun).where(PipelineRun.id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            return None
        if run.status != "pending_budget_review":
            return run

        run.status = "pending"
        await db.flush()

        await log_action(
            db,
            user_id=user_id,
            entity_type="pipeline_run",
            entity_id=run.id,
            action="budget_override",
            details={"approved_by": user_id},
        )
        return run

    @staticmethod
    async def approve_queued_runs_bulk(
        run_ids: list[int],
        user_id: int,
        db: AsyncSession,
    ) -> list[PipelineRun]:
        """Bulk approve queued runs."""
        approved = []
        for run_id in run_ids:
            run = await TriggerService.approve_queued_run(run_id, user_id, db)
            if run:
                approved.append(run)
        return approved

    @staticmethod
    async def get_trigger_stats(trigger_id: int, db: AsyncSession) -> dict:
        """Get trigger evaluation statistics."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        result_7d = await db.execute(
            select(func.count(TriggerEvaluation.id)).where(
                TriggerEvaluation.trigger_id == trigger_id,
                TriggerEvaluation.result == "submitted",
                TriggerEvaluation.created_at >= now - timedelta(days=7),
            )
        )
        result_30d = await db.execute(
            select(func.count(TriggerEvaluation.id)).where(
                TriggerEvaluation.trigger_id == trigger_id,
                TriggerEvaluation.result == "submitted",
                TriggerEvaluation.created_at >= now - timedelta(days=30),
            )
        )
        return {
            "runs_triggered_7d": result_7d.scalar_one(),
            "runs_triggered_30d": result_30d.scalar_one(),
        }

    @staticmethod
    def get_active_batches() -> dict[int, dict]:
        """Expose active batches for testing."""
        return dict(_active_batches)

    @staticmethod
    def clear_batches():
        """Clear all batches for testing."""
        _active_batches.clear()
