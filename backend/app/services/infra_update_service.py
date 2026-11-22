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
from contextlib import aclosing
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StateError
from app.platform.platform_config_service import PlatformConfigService
from app.services.terraform_executor import TerraformExecutor
from app.services.terraform_stream import INTERRUPTED_EVENT, commit_interruption

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
# (e.g. the Literature bucket) land before compute changes. image_build sits
# between them (it is the AWS analog of Cloud Build infra and is deployed after
# storage, before compute); its marker is the CodeBuild project NAME rather than a
# "true" flag, and it is set only on AWS, so image_build is never a candidate on
# GCP (which has no image_build module). Including it lets a newly added resource
# on that module -- e.g. the CodeBuild role's Packer EC2 permissions for work-node
# AMI builds -- land via this flow instead of a full compute teardown + redeploy.
_CANDIDATE_MODULES: tuple[tuple[str, str], ...] = (
    ("storage", "storage_deployed"),
    ("image_build", "aws_codebuild_project"),
    ("compute", "compute_deployed"),
)

# A module marker counts as "deployed" when it is set to a real value: the
# storage/compute markers are "true"/"false"; the image_build marker is a project
# name. These values mean NOT deployed.
_UNSET_MARKERS = frozenset({None, "", "null", "false"})

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

    Reads the recorded raw bucket name from platform_config. Terraform outputs
    are not identity, so an unrecorded name is never taken from them. Returns
    the values it changed, or None when nothing needed changing or the name
    could not be parsed."""
    name = await PlatformConfigService.get(session, "raw_bucket_name")
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
    cfg = await PlatformConfigService.get_many(
        session,
        ["terraform_initialized"] + [flag for _module, flag in _CANDIDATE_MODULES],
    )
    if cfg.get("terraform_initialized") != "true":
        raise StateError("Terraform has not been initialized")
    modules = [module for module, flag in _CANDIDATE_MODULES if cfg.get(flag) not in _UNSET_MARKERS]
    if not modules:
        raise StateError("No infrastructure is deployed")
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
        # An approved update gives existing buckets bioAF's identity markers when they have none.
        # After realigning: an install may have its storage generation recorded only just now.
        from app.services.storage_generation import ensure_markers

        await ensure_markers(session, origin="update")
        # Persist current storage bucket names from live state so platform_config
        # reflects what is actually deployed. This self-heals an instance whose
        # bucket (e.g. literature) exists but whose name was never recorded, so
        # uploads work and the Components view lists every bucket. Best-effort.
        await _persist_module_outputs(session, "storage")

    # Persist compute outputs (cluster identity + per-workload runner SA emails)
    # from live state too, so a runner SA added by a code update -- e.g. the
    # cellxgene runner -- is recorded in platform_config even when this check
    # finds nothing left to apply. Without this the adapter never learns the SA
    # email and can't wire Workload Identity for the pod. Best-effort.
    if "compute" in modules:
        await _persist_module_outputs(session, "compute")

    module_results: list[dict] = []
    modules_with_additive: list[str] = []
    additive: list[dict] = []
    destructive: list[dict] = []
    stateful_destructive_present = False

    for module in modules:
        run = await TerraformExecutor.run_plan(session, user_id, module_name=module)
        if run.status == "failed":
            raise StateError(f"Plan failed for {module}: {run.error_message or 'unknown error'}")

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


class TargetApplyFailed(Exception):
    """A targeted apply failed for a reason other than a classified benign no-op."""

    def __init__(self, module: str, target: str):
        super().__init__(f"{module}: {target}")
        self.module = module
        self.target = target


class TargetPlanFailed(TargetApplyFailed):
    """The plan for one target failed, so whether it still needs applying is unknown."""


class TargetOutOfScope(TargetApplyFailed):
    """Applying the target would also change something the confirmed update may not change."""

    def __init__(self, module: str, target: str, offending: list[str]):
        super().__init__(module, target)
        self.offending = offending


def _out_of_scope(plan_json: dict | None, allowed: set[str]) -> list[str]:
    """What a targeted plan would change beyond the confirmed additive scope.

    A targeted apply also applies the target's dependencies, so ``-target`` alone does not keep
    an update additive: every change in the targeted plan must be an additive change the update
    already enumerated, and nothing may be deleted or replaced.
    """
    offending = [r["address"] for r in list_destructive(plan_json)]
    offending += [r["address"] for r in additive_resources(plan_json) if r["address"] not in allowed]
    return offending


def update_summary(added: int) -> str:
    """The finished-update line signed off in mockup M1."""
    if added == 0:
        return "Your infrastructure is up to date."
    return f"Updates applied: {added} resource{'s' if added != 1 else ''} added."


async def apply_modules_sequentially(modules: list[str], user_id: int, handle=None):
    """Re-plan each module and apply ONLY its additive resources, one resource
    at a time (targeted), one module at a time. Never applies a delete/replace.

    Applying each additive resource on its own run isolates a classified benign
    no-op update (GCP rejects it with "Must specify a field to update"): it is
    logged and skipped. Any other failure stops the batch: no later target or
    module starts, and the outcome says what was applied before it.

    ``handle`` is the infrastructure operation's handle when this runs as one;
    returns the operation outcome either way."""
    from app.database import async_session_factory
    from app.services.infrastructure_operations import failed, succeeded

    if async_session_factory is None:  # pragma: no cover - app not initialized
        return failed("The application is not ready.", "not_ready")

    added = 0
    for module in modules:
        label = f"Applying {module} changes"
        if handle is not None:
            await handle.step(f"plan:{module}", f"Planning {module} changes", "running")
        # Plan once to enumerate the additive resources, then retire the run.
        async with async_session_factory() as s:  # type: ignore[misc]
            try:
                run = await TerraformExecutor.run_plan(s, user_id, module_name=module)
                if run.status != "awaiting_confirmation":
                    await s.commit()
                    raise TargetApplyFailed(module, "plan")
                targets = additive_addresses(run.plan_json)
                run.status = "cancelled"
                run.completed_at = datetime.now(timezone.utc)
                await s.commit()
            except TargetApplyFailed:
                if handle is not None:
                    await handle.step(f"plan:{module}", state="failed", detail="Planning failed.")
                return failed(
                    f"Planning {module} changes failed. Nothing else was applied.", "plan_failed", added=added
                )
            except asyncio.CancelledError:
                await s.rollback()
                raise
            except Exception:
                logger.exception("Infrastructure update plan failed for module %s", module)
                await s.rollback()
                if handle is not None:
                    await handle.step(f"plan:{module}", state="failed", detail="Planning failed.")
                return failed(
                    f"Planning {module} changes failed. Nothing else was applied.", "plan_failed", added=added
                )
        if handle is not None:
            await handle.step(f"plan:{module}", state="done")
            await handle.step(f"apply:{module}", label, "running", completed=0, total=len(targets))

        applied_any = False
        allowed = set(targets)
        for index, target in enumerate(targets, start=1):
            try:
                applied = await _apply_one_target(module, target, user_id, allowed)
            except TargetOutOfScope as exc:
                if handle is not None:
                    await handle.step(
                        f"apply:{module}",
                        state="failed",
                        detail="It would also delete or replace an existing resource.",
                    )
                return failed(
                    f"Applying {target} would also delete or replace an existing resource, so it was not applied. "
                    "Nothing else was applied.",
                    "scope_exceeded",
                    added=added,
                    failed_target=target,
                    out_of_scope=exc.offending,
                )
            except TargetPlanFailed:
                if handle is not None:
                    await handle.step(f"apply:{module}", state="failed", detail=f"Could not plan {target}.")
                return failed(
                    f"Planning {module} changes failed. Nothing else was applied.",
                    "plan_failed",
                    added=added,
                    failed_target=target,
                )
            except TargetApplyFailed:
                if handle is not None:
                    await handle.step(f"apply:{module}", state="failed", detail=f"Could not apply {target}.")
                return failed(
                    f"Applying {module} changes failed. Nothing else was applied.",
                    "apply_failed",
                    added=added,
                    failed_target=target,
                )
            applied_any = applied or applied_any
            added += 1 if applied else 0
            if handle is not None:
                await handle.step(f"apply:{module}", completed=index, total=len(targets))

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
        if handle is not None:
            await handle.step(f"apply:{module}", state="done")
    return succeeded(update_summary(added), added=added)


async def _apply_one_target(module: str, target: str, user_id: int, allowed: set[str] | None = None) -> bool:
    """Plan exactly what applying ``target`` would change, check it stays within the confirmed
    additive scope, and apply it. Returns True when a real change was applied.

    A benign no-op update (GCP "Must specify a field to update") is logged and
    treated as applied-nothing (returns False) instead of failing, so it cannot
    abort the surrounding batch or strand the module. A targeted plan that would
    delete, replace, or change anything outside ``allowed`` raises TargetOutOfScope
    before anything is applied."""
    from app.database import async_session_factory

    if async_session_factory is None:  # pragma: no cover - app not initialized
        return False

    async with async_session_factory() as s:  # type: ignore[misc]
        applying = False
        try:
            run = await TerraformExecutor.run_plan(s, user_id, module_name=module, targets=[target])
            if run.status != "awaiting_confirmation":
                # The plan failed: whether this target still needs applying is unknown.
                await s.commit()
                raise TargetPlanFailed(module, target)
            # The target may have already been applied (or turned destructive)
            # since enumeration; only apply it while it is still additive.
            if target not in set(additive_addresses(run.plan_json)):
                run.status = "cancelled"
                run.completed_at = datetime.now(timezone.utc)
                await s.commit()
                return False
            offending = _out_of_scope(run.plan_json, allowed if allowed is not None else {target})
            if offending:
                run.status = "cancelled"
                run.completed_at = datetime.now(timezone.utc)
                await s.commit()
                logger.warning("Infrastructure update: %s would also change %s; not applied", target, offending)
                raise TargetOutOfScope(module, target, offending)
            run.status = "applying"
            await s.flush()
            applying = True
            interrupted = False
            async with aclosing(TerraformExecutor.run_apply(s, run.id, user_id, targets=[target])) as events:
                async for event in events:
                    interrupted = interrupted or event.event_type == INTERRUPTED_EVENT
            if interrupted:
                # run_apply absorbed the cancellation. Raise it again so the batch
                # does not start another Terraform process.
                raise asyncio.CancelledError
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
            if run.status == "failed":
                raise TargetApplyFailed(module, target)
            return applied
        except asyncio.CancelledError:
            if applying:
                await commit_interruption(s)
            else:
                # Nothing ran yet: leave no half-planned run behind.
                await s.rollback()
            raise
        except TargetApplyFailed:
            raise
        except Exception as exc:
            logger.exception("Infrastructure update apply failed for target %s", target)
            await s.rollback()
            raise TargetApplyFailed(module, target) from exc


def check_summary(added: int, destructive: list[dict]) -> str:
    """The finished-update line signed off in mockup M1."""
    skipped = len(destructive)
    parts = [] if added == 0 and skipped else [update_summary(added)]
    if skipped:
        parts.append(
            f"{skipped} change{' was' if skipped == 1 else 's were'} not applied because "
            f"{'it' if skipped == 1 else 'they'} would delete or replace an existing resource."
        )
    return " ".join(parts)


async def run_update_check(handle, user_id: int):
    """The confirmed "Check for infrastructure updates" action (M1), as one operation.

    Plans every deployed module, then applies only the additive changes automatically, with no
    second approval. Changes that would delete or replace a resource are listed in the outcome
    and never applied.
    """
    from app.database import async_session_factory
    from app.services.infrastructure_operations import OperationResult, succeeded

    from app.services import infrastructure_definitions as definitions

    await handle.step("plan", "Planning changes", "running")
    async with async_session_factory() as s:  # type: ignore[misc]
        # The definitions this update reconciles, captured before anything runs (M17).
        captured = await definitions.capture(s)
        # Never give a confirmed bucket's marker to a bucket that replaced it.
        from app.services.storage_identity import require_confirmed_buckets_unchanged

        if await PlatformConfigService.get(s, "storage_deployed") not in _UNSET_MARKERS:
            await require_confirmed_buckets_unchanged(s)
            await s.commit()
        result = await check_for_updates(s, user_id)
        await s.commit()
    await handle.step("plan", state="done")
    destructive = [
        {k: r.get(k) for k in ("address", "type", "action", "description", "stateful")}
        for r in result["destructive_resources"]
    ]
    added = 0
    if result["has_additive"]:
        applied = await apply_modules_sequentially(result["modules_with_additive"], user_id, handle)
        if applied.status != "succeeded":
            return OperationResult(
                applied.status,
                applied.message,
                applied.category,
                {**(applied.partial_effects or {}), "destructive": destructive},
            )
        added = (applied.partial_effects or {}).get("added", 0)
    # Every planned module is now reconciled: its additive changes applied, and any remaining
    # delete/replace changes recorded as pending rather than marked current.
    async with async_session_factory() as s:  # type: ignore[misc]
        for planned in result["modules"]:
            await definitions.record_reconciled(
                s,
                captured,
                planned["module"],
                destructive_pending=bool(planned["destroy_count"]),
                operation_id=handle.id,
            )
        await definitions.refresh_notice(s)
        await s.commit()
    return succeeded(check_summary(added, destructive), added=added, destructive=destructive)


def launch_update_check(user_id: int, *, operation_id: int) -> None:
    """Run the confirmed update check in the background as the accepted operation (seam)."""
    from app.services.infrastructure_operations import spawn_operation

    spawn_operation(operation_id, lambda handle: run_update_check(handle, user_id), role="infra-update-check")


def launch_background_apply(modules: list[str], user_id: int, *, operation_id: int) -> None:
    """Run the additive-only apply in the background as the accepted operation.
    Isolated as a seam so endpoints can trigger it and tests can assert it was
    scheduled."""
    from app.services.infrastructure_operations import spawn_operation

    spawn_operation(
        operation_id,
        lambda handle: apply_modules_sequentially(modules, user_id, handle),
        role="infra-update-apply",
    )
