import asyncio
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError, StateError
from app.models.component import ComponentState, TerraformRun
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import TERRAFORM_APPLY_FAILURE

logger = logging.getLogger("bioaf.terraform")

TERRAFORM_DIR = Path("/app/terraform")
TFVARS_FILE = TERRAFORM_DIR / "terraform.tfvars"

# Simple lock for one-operation-at-a-time
_tf_lock = asyncio.Lock()


class TerraformService:
    @staticmethod
    async def get_active_run(session: AsyncSession) -> TerraformRun | None:
        result = await session.execute(
            select(TerraformRun).where(
                TerraformRun.status.in_(["pending", "planning", "awaiting_confirmation", "applying"])
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_runs(session: AsyncSession, limit: int = 50) -> list[TerraformRun]:
        result = await session.execute(select(TerraformRun).order_by(TerraformRun.started_at.desc()).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def get_run(session: AsyncSession, run_id: int) -> TerraformRun | None:
        result = await session.execute(select(TerraformRun).where(TerraformRun.id == run_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def generate_plan(
        session: AsyncSession,
        user_id: int,
        component_key: str | None,
        config_changes: dict | None = None,
    ) -> TerraformRun:
        # Check for active runs
        active = await TerraformService.get_active_run(session)
        if active:
            raise ConflictError(f"Another Terraform operation is in progress (run {active.id})")

        # Create run record
        run = TerraformRun(
            triggered_by_user_id=user_id,
            action="plan",
            component_key=component_key,
            status="planning",
        )
        session.add(run)
        await session.flush()

        # Update tfvars if config changes provided
        if config_changes:
            TerraformService._update_tfvars(config_changes)

        # Run terraform plan
        try:
            plan_output = await TerraformService._run_plan()
            plan_summary = TerraformService._parse_plan_json(plan_output)
            run.plan_summary_json = plan_summary
            run.status = "awaiting_confirmation"
        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
            run.completed_at = datetime.now(timezone.utc)
            logger.error("Terraform plan failed: %s", e)

        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="terraform",
            entity_id=run.id,
            action="plan",
            details={"component_key": component_key, "status": run.status},
        )
        return run

    @staticmethod
    async def apply_plan(
        session: AsyncSession,
        run_id: int,
        user_id: int,
    ) -> TerraformRun:
        run = await TerraformService.get_run(session, run_id)
        if not run:
            raise NotFoundError(f"Run {run_id} not found")
        if run.status != "awaiting_confirmation":
            raise StateError(f"Run {run_id} is not awaiting confirmation (status: {run.status})")

        run.status = "applying"
        await session.flush()

        try:
            async with _tf_lock:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["terraform", "apply", "-auto-approve", "-no-color"],
                    cwd=str(TERRAFORM_DIR),
                    capture_output=True,
                    text=True,
                    timeout=1800,
                )

            if result.returncode == 0:
                run.status = "completed"
                # Update component state if applicable
                if run.component_key:
                    comp_state = await session.execute(
                        select(ComponentState).where(ComponentState.component_key == run.component_key)
                    )
                    comp = comp_state.scalar_one_or_none()
                    if comp:
                        comp.status = "running" if comp.enabled else "disabled"
                        comp.last_terraform_run_id = run.id
            else:
                run.status = "failed"
                run.error_message = result.stderr

            run.completed_at = datetime.now(timezone.utc)
        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
            run.completed_at = datetime.now(timezone.utc)

        await session.flush()

        # Commit tfvars to GitOps repo after successful apply
        if run.status == "completed" and TFVARS_FILE.exists():
            try:
                from app.services.gitops_service import GitOpsService

                repo = await GitOpsService.get_repo(session, run.triggered_by_user_id)
                if repo is None:
                    # Try with org_id from component state
                    pass
                else:
                    tfvars_content = TFVARS_FILE.read_text()
                    component_desc = run.component_key or "infrastructure"
                    await GitOpsService.commit_and_push(
                        session,
                        repo.organization_id,
                        user_id,
                        files={"terraform/terraform.tfvars": tfvars_content},
                        message=f"terraform: apply {component_desc}",
                    )
            except Exception as e:
                logger.warning("Failed to commit tfvars to GitOps: %s", e)

        if run.status == "failed":
            asyncio.create_task(
                event_bus.emit(
                    TERRAFORM_APPLY_FAILURE,
                    {
                        "event_type": TERRAFORM_APPLY_FAILURE,
                        "org_id": 1,  # Terraform runs are global
                        "user_id": user_id,
                        "entity_type": "terraform_run",
                        "entity_id": run.id,
                        "title": f"Terraform apply failed for {run.component_key or 'infrastructure'}",
                        "message": run.error_message or "Unknown error",
                        "severity": "critical",
                        "summary": f"Terraform apply failed for {run.component_key or 'infrastructure'}",
                    },
                )
            )

        await log_action(
            session,
            user_id=user_id,
            entity_type="terraform",
            entity_id=run.id,
            action="apply",
            details={"status": run.status, "component_key": run.component_key},
        )
        return run

    @staticmethod
    async def cancel_run(
        session: AsyncSession,
        run_id: int,
        user_id: int,
    ) -> TerraformRun:
        run = await TerraformService.get_run(session, run_id)
        if not run:
            raise NotFoundError(f"Run {run_id} not found")
        if run.status != "awaiting_confirmation":
            raise StateError(f"Run {run_id} cannot be cancelled (status: {run.status})")

        run.status = "cancelled"
        run.completed_at = datetime.now(timezone.utc)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="terraform",
            entity_id=run.id,
            action="cancel",
            details={"component_key": run.component_key},
        )
        return run

    @staticmethod
    def _update_tfvars(config_changes: dict) -> None:
        """Update terraform.tfvars with new values."""
        if not TFVARS_FILE.exists():
            TFVARS_FILE.write_text("")

        existing = TFVARS_FILE.read_text()
        lines = existing.splitlines()

        for key, value in config_changes.items():
            # Format the value based on type
            if isinstance(value, bool):
                formatted = "true" if value else "false"
            elif isinstance(value, int | float):
                formatted = str(value)
            else:
                formatted = f'"{value}"'

            # Update existing line or append
            updated = False
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(f"{key}") and "=" in stripped:
                    lines[i] = f"{key} = {formatted}"
                    updated = True
                    break

            if not updated:
                lines.append(f"{key} = {formatted}")

        TFVARS_FILE.write_text("\n".join(lines) + "\n")

    @staticmethod
    async def _run_plan() -> str:
        """Run terraform plan and return JSON output."""
        async with _tf_lock:
            result = await asyncio.to_thread(
                subprocess.run,
                ["terraform", "plan", "-out=plan.tfplan", "-json", "-no-color"],
                cwd=str(TERRAFORM_DIR),
                capture_output=True,
                text=True,
                timeout=600,
            )

        if result.returncode != 0:
            raise RuntimeError(f"Terraform plan failed: {result.stderr}")
        return result.stdout

    @staticmethod
    def _parse_plan_json(plan_output: str) -> dict:
        """Parse Terraform JSON plan output into a human-readable summary."""
        resources_to_add: list[dict] = []
        resources_to_change: list[dict] = []
        resources_to_destroy: list[dict] = []

        for line in plan_output.strip().splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") == "resource_drift" or entry.get("type") == "planned_change":
                change = entry.get("change", {})
                resource = change.get("resource", {})
                action = change.get("action")
                resource_info = {
                    "type": resource.get("resource_type", "unknown"),
                    "name": resource.get("resource_name", "unknown"),
                    "address": resource.get("addr", ""),
                }

                if action == "create":
                    resources_to_add.append(resource_info)
                elif action == "update":
                    resources_to_change.append(resource_info)
                elif action == "delete":
                    resources_to_destroy.append(resource_info)

        return {
            "add": resources_to_add,
            "change": resources_to_change,
            "destroy": resources_to_destroy,
            "add_count": len(resources_to_add),
            "change_count": len(resources_to_change),
            "destroy_count": len(resources_to_destroy),
        }
