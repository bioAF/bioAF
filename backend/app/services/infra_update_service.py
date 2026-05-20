"""Infrastructure update check + apply.

Backs the "Check for Infrastructure Updates" action. It re-plans every
deployed Terraform module against the current module code (the normal deploy
flow skips already-deployed modules, so a newly added resource such as the
Literature bucket is never created by re-deploying). Changes that only create
or update resources are applied automatically; a delete or replace of a
stateful (data-bearing) resource is flagged and held for explicit approval.

Lifecycle note: TerraformExecutor.run_plan recovers "stale" runs by failing
any non-terminal run that has no live process. So the check phase retires
each plan run as soon as it has read the plan, and the apply phase re-plans
and applies one module at a time to completion, never leaving a second
module's plan lingering while another is in flight.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.terraform_executor import TerraformExecutor

logger = logging.getLogger("bioaf.infra_update_service")

# Resource types whose delete or replace destroys persistent data. A plan that
# touches one of these with a destroy/replace must be approved by a human
# before it is applied; everything else applies automatically.
STATEFUL_RESOURCE_TYPES = frozenset(
    {
        "google_storage_bucket",
        "google_bigquery_dataset",
        "google_bigquery_table",
        "google_sql_database_instance",
        "google_sql_database",
        "google_filestore_instance",
    }
)

DESTRUCTIVE_ACTIONS = frozenset({"delete", "replace"})

# Modules the check covers, in apply order. Storage first so data buckets
# (e.g. the Literature bucket) land before compute changes.
_CANDIDATE_MODULES: tuple[tuple[str, str], ...] = (
    ("storage", "storage_deployed"),
    ("compute", "compute_deployed"),
)


def classify_destructive(plan_json: dict | None) -> list[dict]:
    """Return the planned changes that would destroy or replace a stateful
    resource. An empty list means the plan is safe to apply automatically."""
    if not plan_json:
        return []
    return [
        r
        for r in plan_json.get("resources", [])
        if r.get("action") in DESTRUCTIVE_ACTIONS and r.get("type") in STATEFUL_RESOURCE_TYPES
    ]


async def _deployed_modules(session: AsyncSession) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('terraform_initialized', 'storage_deployed', 'compute_deployed')"
            )
        )
    ).fetchall()
    cfg = {r[0]: r[1] for r in rows}
    if cfg.get("terraform_initialized") != "true":
        raise ValueError("Terraform has not been initialized")
    modules = [module for module, flag in _CANDIDATE_MODULES if cfg.get(flag) == "true"]
    if not modules:
        raise ValueError("No infrastructure is deployed")
    return modules


async def check_for_updates(session: AsyncSession, user_id: int) -> dict:
    """Plan every deployed module and report what would change.

    Returns a dict with the per-module summary, the aggregate destructive
    resource list, whether there are any changes, and whether approval is
    required (a stateful destroy/replace is present). Does not apply anything;
    the caller decides whether to launch the apply.
    """
    modules = await _deployed_modules(session)

    module_results: list[dict] = []
    modules_with_changes: list[str] = []
    destructive: list[dict] = []

    for module in modules:
        run = await TerraformExecutor.run_plan(session, user_id, module_name=module)
        if run.status == "failed":
            raise ValueError(f"Plan failed for {module}: {run.error_message or 'unknown error'}")

        plan = run.plan_json or {}
        total = plan.get("total", 0)
        mod_destructive = classify_destructive(plan)
        module_results.append(
            {
                "module": module,
                "add_count": plan.get("add_count", 0),
                "change_count": plan.get("change_count", 0),
                "destroy_count": plan.get("destroy_count", 0),
                "has_changes": total > 0,
                "destructive_resources": mod_destructive,
            }
        )
        if total > 0:
            modules_with_changes.append(module)
            destructive.extend(mod_destructive)

        # Retire the plan run immediately: it has served its purpose (we read
        # plan_json), and leaving it non-terminal would let the next module's
        # plan recovery fail it. The apply phase re-plans from scratch.
        run.status = "cancelled"
        run.completed_at = datetime.now(timezone.utc)
        await session.flush()

    return {
        "has_changes": bool(modules_with_changes),
        "requires_approval": bool(destructive),
        "modules": module_results,
        "modules_with_changes": modules_with_changes,
        "destructive_resources": destructive,
    }


async def apply_modules_sequentially(modules: list[str], user_id: int) -> None:
    """Re-plan and apply each module to completion, one at a time.

    Each module gets its own session and reaches a terminal state before the
    next is planned, so run_plan's stale-run recovery never trips on an
    in-flight sibling. Failures are logged; remaining modules still run.
    """
    from app.database import async_session_factory

    if async_session_factory is None:  # pragma: no cover - app not initialized
        return

    for module in modules:
        async with async_session_factory() as s:  # type: ignore[misc]
            try:
                run = await TerraformExecutor.run_plan(s, user_id, module_name=module)
                if run.status != "awaiting_confirmation":
                    await s.commit()
                    continue
                if not (run.plan_json and run.plan_json.get("total", 0) > 0):
                    # Nothing to do for this module anymore; retire the run.
                    run.status = "cancelled"
                    run.completed_at = datetime.now(timezone.utc)
                    await s.commit()
                    continue
                run.status = "applying"
                await s.flush()
                async for _event in TerraformExecutor.run_apply(s, run.id, user_id):
                    pass
                await s.commit()
            except Exception:
                logger.exception("Infrastructure update apply failed for module %s", module)
                await s.rollback()


def launch_background_apply(modules: list[str], user_id: int) -> None:
    """Kick off the sequential apply in the background. Isolated as a seam so
    endpoints can trigger it and tests can assert it was scheduled."""
    asyncio.create_task(apply_modules_sequentially(modules, user_id))
