"""Infrastructure update check + apply.

Backs the "Check for Infrastructure Updates" action. It re-plans every
deployed Terraform module against the current module code (the normal deploy
flow skips already-deployed modules, so a newly added resource such as the
Literature bucket is never created by re-deploying).

Two safety properties:

1. Re-align first. Bucket names embed org_slug + stack_uid, both immutable.
   If those drifted from the live deployment, a plan would try to *rename*
   (i.e. replace = destroy + recreate) every existing bucket. Before planning,
   we read a real deployed bucket name and restore the matching org_slug /
   stack_uid, so the plan stops trying to rename existing resources and a
   newly added resource shows up as the only change.

2. Additive-only apply. The apply only ever targets resources the plan would
   create or update (`terraform apply -target=...`). A delete or replace is
   never applied by this flow, so existing data buckets cannot be destroyed
   even if a plan still wants to.

Lifecycle note: TerraformExecutor.run_plan recovers "stale" runs by failing
any non-terminal run that has no live process. So the check phase retires
each plan run as soon as it has read the plan, and the apply phase re-plans
one module at a time to completion.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.terraform_executor import TerraformExecutor

logger = logging.getLogger("bioaf.infra_update_service")

# Resource types whose delete or replace destroys persistent data.
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
ADDITIVE_ACTIONS = frozenset({"create", "update"})

# A targeted apply of an in-place "update" can reduce to a no-op at the GCP API
# (e.g. a perpetual provider diff such as a node pool's node_locations being
# normalised back to the cluster's zones). GCP rejects the empty update with
# HTTP 400 "Must specify a field to update". The update changes nothing, so the
# apply flow treats this as a benign no-op rather than a failure that would
# abort the rest of the batch and recur on every subsequent check.
_BENIGN_APPLY_DIAGNOSTICS: tuple[str, ...] = ("Must specify a field to update",)

# Modules the check covers, in apply order. Storage first so data buckets
# (e.g. the Literature bucket) land before compute changes.
_CANDIDATE_MODULES: tuple[tuple[str, str], ...] = (
    ("storage", "storage_deployed"),
    ("compute", "compute_deployed"),
)

# Terraform-generated stack uid is secrets.token_hex(3): six lowercase hex.
_STACK_UID_RE = re.compile(r"^[0-9a-f]{6}$")


# ---------------------------------------------------------------------------
# Pure plan helpers
# ---------------------------------------------------------------------------


def classify_destructive(plan_json: dict | None) -> list[dict]:
    """Planned changes that would destroy or replace a *stateful* resource."""
    if not plan_json:
        return []
    return [
        r
        for r in plan_json.get("resources", [])
        if r.get("action") in DESTRUCTIVE_ACTIONS and r.get("type") in STATEFUL_RESOURCE_TYPES
    ]


def list_destructive(plan_json: dict | None) -> list[dict]:
    """Every delete/replace in the plan, each annotated with whether it is a
    stateful (data-bearing) resource."""
    if not plan_json:
        return []
    out: list[dict] = []
    for r in plan_json.get("resources", []):
        if r.get("action") in DESTRUCTIVE_ACTIONS:
            out.append({**r, "stateful": r.get("type") in STATEFUL_RESOURCE_TYPES})
    return out


def additive_resources(plan_json: dict | None) -> list[dict]:
    """Plan changes that create or update a resource (never destructive).
    Data-source reads (address starts with 'data.') are excluded."""
    if not plan_json:
        return []
    return [
        r
        for r in plan_json.get("resources", [])
        if r.get("action") in ADDITIVE_ACTIONS and not str(r.get("address", "")).startswith("data.")
    ]


def additive_addresses(plan_json: dict | None) -> list[str]:
    return [r["address"] for r in additive_resources(plan_json)]


# ---------------------------------------------------------------------------
# Re-align deployed naming
# ---------------------------------------------------------------------------


def _parse_bucket_name(name: str, purpose: str) -> tuple[str, str] | None:
    """Parse `bioaf-{purpose}-{org_slug}-{stack_uid}` into (org_slug, stack_uid).

    org_slug may contain hyphens; stack_uid is the trailing six-hex segment.
    Returns None when the name does not match the current naming scheme (e.g.
    an older deployment without a stack_uid suffix), so the caller can fall
    back to the additive-only apply instead of guessing."""
    prefix = f"bioaf-{purpose}-"
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix) :]
    if "-" not in rest:
        return None
    org_slug, stack_uid = rest.rsplit("-", 1)
    if not org_slug or not _STACK_UID_RE.match(stack_uid):
        return None
    return org_slug, stack_uid


async def realign_storage_naming(session: AsyncSession) -> dict | None:
    """Pin storage_stack_uid in platform_config to match the live deployed
    buckets, so a storage plan does not try to rename (replace) them.

    Critically, this writes the storage-specific key (storage_stack_uid), NOT
    the shared deploy_suffix. Storage and compute can carry different suffixes
    (e.g. buckets at -4bd459 while the GKE cluster is at -41aae5); clobbering
    the shared deploy_suffix with the storage value would make compute plans
    want to replace the live cluster. org_slug is shared and identical for both,
    so it is safe to align.

    Reads a known deployed bucket name (raw) from platform_config, falling back
    to live Terraform outputs. Returns the values it changed, or None when
    nothing needed changing or the name could not be parsed."""
    from app.services.platform_config_service import PlatformConfigService

    name = await PlatformConfigService.get(session, "raw_bucket_name")
    if not name or name == "null":
        try:
            outputs = await TerraformExecutor.read_module_outputs(session, "storage")
            entry = outputs.get("raw_bucket_name") or {}
            name = entry.get("value") if isinstance(entry, dict) else None
        except Exception as exc:  # pragma: no cover - depends on live state
            logger.warning("realign: could not read storage outputs: %s", exc)
            name = None
    if not name or name == "null":
        return None

    parsed = _parse_bucket_name(name, "raw")
    if not parsed:
        return None
    org_slug, stack_uid = parsed

    changed: dict[str, str] = {}
    if (await PlatformConfigService.get(session, "org_slug")) != org_slug:
        await PlatformConfigService.set(session, "org_slug", org_slug)
        changed["org_slug"] = org_slug
    if (await PlatformConfigService.get(session, "storage_stack_uid")) != stack_uid:
        await PlatformConfigService.set(session, "storage_stack_uid", stack_uid)
        changed["stack_uid"] = stack_uid
    return changed or None


# ---------------------------------------------------------------------------
# Check + apply orchestration
# ---------------------------------------------------------------------------


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
    """Re-align naming, plan every deployed module, and report what would change.

    The apply (when launched) only ever applies additive resources, so the
    report separates additive changes (which can be applied) from destructive
    ones (delete/replace, which this flow never applies).
    """
    modules = await _deployed_modules(session)

    realigned = None
    if "storage" in modules:
        realigned = await realign_storage_naming(session)
        # Persist current storage bucket names from live state so platform_config
        # reflects what is actually deployed. This self-heals an instance whose
        # bucket (e.g. literature) exists but whose name was never recorded, so
        # uploads work and the Components view lists every bucket. Best-effort.
        await _persist_module_outputs(session, "storage")

    module_results: list[dict] = []
    modules_with_additive: list[str] = []
    additive: list[dict] = []
    destructive: list[dict] = []
    stateful_destructive_present = False

    for module in modules:
        run = await TerraformExecutor.run_plan(session, user_id, module_name=module)
        if run.status == "failed":
            raise ValueError(f"Plan failed for {module}: {run.error_message or 'unknown error'}")

        plan = run.plan_json or {}
        total = plan.get("total", 0)
        mod_additive = additive_resources(plan)
        mod_destructive = list_destructive(plan)
        module_results.append(
            {
                "module": module,
                "add_count": plan.get("add_count", 0),
                "change_count": plan.get("change_count", 0),
                "destroy_count": plan.get("destroy_count", 0),
                "has_changes": total > 0,
            }
        )
        if mod_additive:
            modules_with_additive.append(module)
            additive.extend(mod_additive)
        if mod_destructive:
            destructive.extend(mod_destructive)
            if any(d["stateful"] for d in mod_destructive):
                stateful_destructive_present = True

        # Retire the plan run immediately: leaving it non-terminal would let the
        # next module's plan recovery fail it. The apply phase re-plans.
        run.status = "cancelled"
        run.completed_at = datetime.now(timezone.utc)
        await session.flush()

    return {
        "realigned": realigned,
        "has_changes": bool(additive or destructive),
        "has_additive": bool(additive),
        "has_destructive": bool(destructive),
        "requires_approval": stateful_destructive_present,
        "modules": module_results,
        "modules_with_additive": modules_with_additive,
        "additive_resources": additive,
        "destructive_resources": destructive,
    }


async def _persist_module_outputs(session: AsyncSession, module: str) -> None:
    """Read the module's Terraform outputs and write them to platform_config so
    the app knows about newly created resources (e.g. a bucket name). Best-effort:
    a failure here must never break the check or apply."""
    try:
        from app.services.stack_deployment import sync_compute_config, sync_storage_config

        if module == "storage":
            await sync_storage_config(session)
        elif module == "compute":
            await sync_compute_config(session)
    except Exception as exc:
        logger.warning("Persisting %s Terraform outputs failed: %s", module, exc)


def _is_benign_apply_error(message: str | None) -> bool:
    """True when an apply 'failure' applied no change: a no-op in-place update
    GCP rejected with a 400 (see _BENIGN_APPLY_DIAGNOSTICS). Such a result must
    not abort the rest of the batch."""
    if not message:
        return False
    return any(token in message for token in _BENIGN_APPLY_DIAGNOSTICS)


async def apply_modules_sequentially(modules: list[str], user_id: int) -> None:
    """Re-plan each module and apply ONLY its additive resources, one resource
    at a time (targeted), one module at a time. Never applies a delete/replace.

    Applying each additive resource on its own run isolates failures: a single
    benign no-op update (which GCP rejects with "Must specify a field to update")
    is logged and skipped, so the real new resources still land and the batch is
    never aborted or stranded by it."""
    from app.database import async_session_factory

    if async_session_factory is None:  # pragma: no cover - app not initialized
        return

    for module in modules:
        # Plan once to enumerate the additive resources, then retire the run.
        async with async_session_factory() as s:  # type: ignore[misc]
            try:
                run = await TerraformExecutor.run_plan(s, user_id, module_name=module)
                targets = additive_addresses(run.plan_json) if run.status == "awaiting_confirmation" else []
                run.status = "cancelled"
                run.completed_at = datetime.now(timezone.utc)
                await s.commit()
            except Exception:
                logger.exception("Infrastructure update plan failed for module %s", module)
                await s.rollback()
                continue

        applied_any = False
        for target in targets:
            applied_any = await _apply_one_target(module, target, user_id) or applied_any

        # Persist new resource names (e.g. the literature bucket) once a real
        # change landed. Best-effort.
        if applied_any:
            async with async_session_factory() as s:  # type: ignore[misc]
                try:
                    await _persist_module_outputs(s, module)
                    await s.commit()
                except Exception:
                    logger.exception("Persisting %s outputs after apply failed", module)
                    await s.rollback()


async def _apply_one_target(module: str, target: str, user_id: int) -> bool:
    """Re-plan the module and apply a single additive target. Returns True when a
    real change was applied.

    A benign no-op update (GCP "Must specify a field to update") is logged and
    treated as applied-nothing (returns False) instead of failing, so it cannot
    abort the surrounding batch or strand the module."""
    from app.database import async_session_factory

    if async_session_factory is None:  # pragma: no cover - app not initialized
        return False

    async with async_session_factory() as s:  # type: ignore[misc]
        try:
            run = await TerraformExecutor.run_plan(s, user_id, module_name=module)
            # The target may have already been applied (or turned destructive)
            # since enumeration; only apply it while it is still additive.
            if run.status != "awaiting_confirmation" or target not in set(additive_addresses(run.plan_json)):
                run.status = "cancelled"
                run.completed_at = datetime.now(timezone.utc)
                await s.commit()
                return False
            run.status = "applying"
            await s.flush()
            async for _event in TerraformExecutor.run_apply(s, run.id, user_id, targets=[target]):
                pass
            if run.status == "failed" and _is_benign_apply_error(run.error_message):
                logger.info(
                    "Infrastructure update: skipping no-op update for %s (%s)",
                    target,
                    run.error_message,
                )
                run.status = "completed"
                run.error_message = None
                await s.commit()
                return False
            applied = run.status == "completed"
            await s.commit()
            return applied
        except Exception:
            logger.exception("Infrastructure update apply failed for target %s", target)
            await s.rollback()
            return False


def launch_background_apply(modules: list[str], user_id: int) -> None:
    """Kick off the additive-only apply in the background. Isolated as a seam
    so endpoints can trigger it and tests can assert it was scheduled."""
    asyncio.create_task(apply_modules_sequentially(modules, user_id))
