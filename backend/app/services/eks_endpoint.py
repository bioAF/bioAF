"""EKS endpoint lifecycle around AWS compute plans and applies (security plan 04C).

The AWS compute hooks of the Terraform seam call into this module:

- ``prepare_run`` before every compute plan and apply: reads the pre-refresh remote state, backfills
  an existing cluster into ``preserve_existing`` from its verified live endpoint, rechecks a
  preserved endpoint or pending cutover against the live cluster, reconciles an earlier failed or
  interrupted cutover, notes whether an existing control-plane log group must be adopted, and
  returns the explicit inputs (``_eks_resolved``) the tfvars carry.
- ``finish_apply`` after a compute apply: commits a cutover only after the live readback matches
  its target, otherwise leaves it for reconciliation. A failure never reverts the mode.
- ``finish_destroy`` after a compute teardown: forgets the destroyed cluster's snapshot.
- ``request_cutover`` / ``cancel_cutover`` / ``current_status`` back the administrator API.

Decisions live in ``eks_endpoint_policy``; AWS reads go through ``app.adapters.eks_control_plane``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError
from app.platform.platform_config_service import PlatformConfigService
from app.services import eks_endpoint_policy as policy
from app.services.deployment_outputs import identifier_value

logger = logging.getLogger(__name__)

RESOLVED_KEY = "_eks_resolved"
STATUS_KEYS = (*policy.CONFIG_KEYS, "aws_region", "aws_account_id", "gke_cluster_name", "compute_deployed")

PENDING = "pending"
APPLYING = "applying"
NEEDS_RECONCILE = "needs_reconcile"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _set(session: AsyncSession, config: dict, key: str, value) -> None:
    stored = value if isinstance(value, str) else json.dumps(value)
    await PlatformConfigService.set(session, key, stored)
    config[key] = stored
    await session.flush()


async def _describe(name: str, region: str | None) -> dict:
    from app.adapters.eks_control_plane import aws

    try:
        return await asyncio.to_thread(aws.describe_cluster, name, region)
    except Exception as exc:
        raise ValidationError(
            f"The backend could not read the live EKS cluster {name} ({exc}). Nothing was recorded or changed; "
            "check the app's eks:DescribeCluster access and retry."
        ) from exc


async def _log_group_exists(name: str, region: str | None) -> bool:
    from app.adapters.eks_control_plane import aws

    try:
        return await asyncio.to_thread(aws.log_group_exists, name, region)
    except Exception as exc:
        raise ValidationError(
            f"The backend could not check for the control-plane log group {name} ({exc}); retry before planning."
        ) from exc


def _has_log_group(state: dict | None) -> bool:
    return any(
        r.get("mode") == "managed" and r.get("type") == "aws_cloudwatch_log_group" and r.get("name") == "control_plane"
        for r in (state or {}).get("resources") or []
    )


def _cluster_name(config: dict, attributes: dict | None) -> str:
    if attributes and attributes.get("name"):
        return attributes["name"]
    org_slug = identifier_value(config.get("org_slug")) or "bioaf"
    suffix = identifier_value(config.get("compute_stack_uid")) or identifier_value(config.get("deploy_suffix"))
    return f"bioaf-{org_slug}-{suffix or 'pending'}"


def _cutover_target(cutover: dict) -> dict:
    if cutover["mode"] == policy.PRIVATE_ONLY:
        return {"endpoint_public_access": False, "endpoint_private_access": True, "public_access_cidrs": []}
    return {
        "endpoint_public_access": True,
        "endpoint_private_access": True,
        "public_access_cidrs": list(cutover.get("public_access_cidrs") or []),
    }


async def _commit_cutover(session: AsyncSession, config: dict, cutover: dict, live: dict) -> None:
    snapshot = {**(cutover.get("snapshot") or {}), **policy.live_endpoint(live)}
    await _set(session, config, policy.MODE_KEY, cutover["mode"])
    if cutover["mode"] == policy.PUBLIC_RESTRICTED:
        await _set(session, config, policy.CIDRS_KEY, list(cutover.get("public_access_cidrs") or []))
    await _set(session, config, policy.SNAPSHOT_KEY, snapshot)
    await _set(session, config, policy.CUTOVER_KEY, "")
    logger.info("EKS endpoint cutover to %s committed for %s", cutover["mode"], snapshot.get("cluster_arn"))


async def _reconcile(session: AsyncSession, config: dict, cutover: dict, live: dict) -> dict | None:
    """Settle a cutover whose apply failed or was interrupted. Returns the cutover still pending."""
    live_endpoint = policy.live_endpoint(live)
    snapshot = cutover.get("snapshot") or {}
    if policy.endpoint_equal(live_endpoint, _cutover_target(cutover)):
        await _commit_cutover(session, config, cutover, live)
        return None
    if policy.endpoint_equal(live_endpoint, policy.live_endpoint(_as_live(snapshot))):
        cutover = {**cutover, "status": PENDING, "apply_run_id": None}
        await _set(session, config, policy.CUTOVER_KEY, cutover)
        return cutover
    raise ValidationError(
        "A previous endpoint cutover did not finish and the cluster's live Kubernetes API endpoint matches "
        "neither the recorded settings nor the cutover target. Review the live endpoint and reconcile it with "
        "the documented recovery procedure before another compute apply; nothing was changed."
    )


def _as_live(snapshot: dict) -> dict:
    return {
        "arn": snapshot.get("cluster_arn"),
        "resourcesVpcConfig": {
            "endpointPublicAccess": snapshot.get("endpoint_public_access"),
            "endpointPrivateAccess": snapshot.get("endpoint_private_access"),
            "publicAccessCidrs": snapshot.get("public_access_cidrs") or [],
        },
    }


async def prepare_run(
    session: AsyncSession,
    config: dict,
    *,
    phase: str,
    run_id: int | None,
    pull_state: Callable[[], Awaitable[dict | None]],
) -> dict:
    """Resolve and record the endpoint state for a compute plan (``phase="plan"``) or apply."""
    config = dict(config)
    state = await pull_state()
    if state is None:
        raise ValidationError(
            "The remote compute state could not be read, so the cluster's identity and endpoint cannot be "
            "verified. Nothing was changed; check the state bucket and retry."
        )
    attributes = policy.state_cluster(state)
    in_state = attributes is not None
    region = config.get("aws_region")
    mode = config.get(policy.MODE_KEY) or None
    cutover = policy._json(config, policy.CUTOVER_KEY)

    if not in_state and config.get("compute_deployed") == "true":
        raise ValidationError(
            "Compute is recorded as deployed but the remote compute state has no EKS cluster. Reconcile the "
            "compute state before planning; the backend will not recreate or adopt a cluster here."
        )

    if in_state:
        live = await _describe(attributes.get("name") or _cluster_name(config, attributes), region)
        if mode is None:
            if phase != "plan":
                raise ValidationError("Run a compute plan first so the existing cluster's endpoint is recorded.")
            snapshot = policy.snapshot_existing_cluster(state, live, config)
            await _set(session, config, policy.SNAPSHOT_KEY, snapshot)
            await _set(session, config, policy.MODE_KEY, policy.PRESERVE_EXISTING)
            mode = policy.PRESERVE_EXISTING
            logger.info("Recorded existing EKS endpoint for %s as preserve_existing", snapshot["cluster_arn"])
        elif cutover:
            if cutover.get("status") in (APPLYING, NEEDS_RECONCILE):
                cutover = await _reconcile(session, config, cutover, live)
            if cutover:
                policy.require_live_matches(cutover.get("snapshot") or {}, live)
                if phase == "plan":
                    cutover = {**cutover, "plan_run_id": run_id}
                    await _set(session, config, policy.CUTOVER_KEY, cutover)
                else:
                    if cutover.get("plan_run_id") != run_id:
                        raise ValidationError(
                            "This apply is not the reviewed cutover plan. Plan the endpoint cutover again and apply that plan."
                        )
                    cutover = {**cutover, "status": APPLYING, "apply_run_id": run_id, "applying_at": _now()}
                    await _set(session, config, policy.CUTOVER_KEY, cutover)
        elif mode == policy.PRESERVE_EXISTING:
            policy.require_live_matches(policy._json(config, policy.SNAPSHOT_KEY) or {}, live)
    elif mode is None and phase == "plan":
        # A fresh cluster: record the default so the next plan does not mistake it for a legacy one.
        policy.compute_tfvars(config, cluster_in_state=False)
        await _set(session, config, policy.MODE_KEY, policy.PRIVATE_ONLY)

    resolved = policy.compute_tfvars(config, cluster_in_state=in_state)
    if not _has_log_group(state) and await _log_group_exists(
        f"/aws/eks/{_cluster_name(config, attributes)}/cluster", region
    ):
        resolved["adopt_control_plane_log_group"] = True
    config[RESOLVED_KEY] = resolved
    return config


def require_same_inputs(planned: dict | None, applying: dict) -> None:
    """An apply recomputes tfvars from config; refuse if the endpoint/logging inputs moved since the plan."""
    keys = [k for k in applying if k.startswith(("endpoint_", "public_access_cidrs", "control_plane_log"))]
    if planned is None:
        return
    moved = [k for k in keys if planned.get(k) != applying.get(k)]
    if moved:
        raise ValidationError(
            f"The EKS endpoint or logging inputs changed since this plan ({', '.join(moved)}). Plan again and review."
        )


async def finish_apply(session: AsyncSession, config: dict, *, run_id: int | None, succeeded: bool) -> None:
    """Commit a cutover after a verified readback, or leave it for reconciliation."""
    fresh = await PlatformConfigService.get_many(session, list(STATUS_KEYS))
    cutover = policy._json(fresh, policy.CUTOVER_KEY)
    if not cutover or cutover.get("status") != APPLYING or cutover.get("apply_run_id") != run_id:
        return
    if succeeded:
        snapshot = cutover.get("snapshot") or {}
        try:
            live = await _describe(snapshot.get("cluster_name") or "", fresh.get("aws_region"))
        except ValidationError:
            live = None
        if live is not None and live.get("arn") == snapshot.get("cluster_arn"):
            endpoint = policy.live_endpoint(live)
            if policy.endpoint_equal(endpoint, _cutover_target(cutover)):
                await _commit_cutover(session, fresh, cutover, live)
                return
            if policy.endpoint_equal(endpoint, policy.live_endpoint(_as_live(snapshot))):
                # A successful apply that did not touch the cluster (the update flow applies one
                # target at a time): the cutover is still pending, nothing to reconcile.
                await _set(session, fresh, policy.CUTOVER_KEY, {**cutover, "status": PENDING, "apply_run_id": None})
                return
    # A failed apply may leave an EKS update still in progress, so the next plan reconciles it.
    await _set(session, fresh, policy.CUTOVER_KEY, {**cutover, "status": NEEDS_RECONCILE})
    logger.warning("EKS endpoint cutover run %s did not verify; it needs reconciliation", run_id)


async def finish_destroy(session: AsyncSession) -> None:
    """After compute teardown: the destroyed cluster's snapshot and preserved mode no longer apply."""
    config = await PlatformConfigService.get_many(session, list(STATUS_KEYS))
    if config.get(policy.MODE_KEY) == policy.PRESERVE_EXISTING:
        await _set(session, config, policy.MODE_KEY, "")
    await _set(session, config, policy.SNAPSHOT_KEY, "")
    await _set(session, config, policy.CUTOVER_KEY, "")


async def current_status(session: AsyncSession) -> dict:
    config = await PlatformConfigService.get_many(session, list(STATUS_KEYS))
    return policy.status(config)


async def request_cutover(
    session: AsyncSession,
    *,
    mode: str,
    public_access_cidrs: list[str] | None,
    user_id: int,
    acknowledged: bool,
) -> dict:
    """Record a reviewed endpoint cutover. It takes effect through the next compute plan and apply."""
    config = await PlatformConfigService.get_many(
        session, [*STATUS_KEYS, "aws_app_vpc_id", "aws_app_security_group_id"]
    )
    if mode not in policy.HARDENED_MODES:
        raise ValidationError(f"A cutover mode must be one of {', '.join(policy.HARDENED_MODES)}.")
    if not acknowledged:
        raise ValidationError(
            "Confirm that the application, node and independent operator recovery paths were verified and the "
            "recovery procedure is ready before scheduling an endpoint cutover."
        )
    snapshot = policy._json(config, policy.SNAPSHOT_KEY)
    if config.get("compute_deployed") != "true" or not snapshot:
        raise ValidationError("An endpoint cutover needs a deployed cluster with a recorded endpoint snapshot.")
    existing = policy._json(config, policy.CUTOVER_KEY)
    if existing and existing.get("status") != PENDING:
        raise ValidationError("A previous cutover is still being applied or reconciled.")
    cidrs = policy.validate_public_cidrs(public_access_cidrs) if mode == policy.PUBLIC_RESTRICTED else []
    # Validate the target with the same rules a plan uses (private-only needs the app network).
    policy.compute_tfvars(
        {**config, policy.CUTOVER_KEY: json.dumps({"mode": mode, "public_access_cidrs": cidrs})},
        cluster_in_state=True,
    )
    live = await _describe(snapshot.get("cluster_name") or "", config.get("aws_region"))
    policy.require_live_matches(snapshot, live)
    cutover = {
        "mode": mode,
        "public_access_cidrs": cidrs,
        "status": PENDING,
        "requested_by_user_id": user_id,
        "requested_at": _now(),
        "snapshot": snapshot,
    }
    await _set(session, config, policy.CUTOVER_KEY, cutover)
    return cutover


async def cancel_cutover(session: AsyncSession) -> None:
    """Drop a pending cutover, only while the live endpoint still matches the recorded snapshot."""
    config = await PlatformConfigService.get_many(session, list(STATUS_KEYS))
    cutover = policy._json(config, policy.CUTOVER_KEY)
    if not cutover:
        return
    snapshot = cutover.get("snapshot") or {}
    live = await _describe(snapshot.get("cluster_name") or "", config.get("aws_region"))
    policy.require_live_matches(snapshot, live)
    await _set(session, config, policy.CUTOVER_KEY, "")


async def update_settings(
    session: AsyncSession,
    *,
    mode: str | None = None,
    public_access_cidrs: list[str] | None = None,
    log_retention_days: int | None = None,
    log_types: list[str] | None = None,
) -> None:
    """Installation settings: logging at any time; the endpoint mode only before a cluster exists.

    A deployed cluster's endpoint moves only through a reviewed cutover, so a mode or CIDR change
    here is refused once compute is deployed.
    """
    config = await PlatformConfigService.get_many(
        session, [*STATUS_KEYS, "aws_app_vpc_id", "aws_app_security_group_id"]
    )
    candidate = dict(config)
    if log_retention_days is not None:
        candidate[policy.RETENTION_KEY] = str(log_retention_days)
    if log_types is not None:
        candidate[policy.LOG_TYPES_KEY] = json.dumps(log_types)
    logging_inputs = policy.compute_logging(candidate)

    if mode is not None or public_access_cidrs is not None:
        if config.get("compute_deployed") == "true" or policy._json(config, policy.SNAPSHOT_KEY):
            raise ValidationError(
                "This installation already has a cluster; change its Kubernetes API endpoint through a reviewed cutover."
            )
        chosen = mode or config.get(policy.MODE_KEY) or policy.PRIVATE_ONLY
        if chosen not in policy.HARDENED_MODES:
            raise ValidationError(f"A new cluster's endpoint mode must be one of {', '.join(policy.HARDENED_MODES)}.")
        candidate[policy.MODE_KEY] = chosen
        if chosen == policy.PUBLIC_RESTRICTED:
            candidate[policy.CIDRS_KEY] = json.dumps(policy.validate_public_cidrs(public_access_cidrs))
        await _set(session, config, policy.MODE_KEY, chosen)
        if chosen == policy.PUBLIC_RESTRICTED:
            await _set(session, config, policy.CIDRS_KEY, candidate[policy.CIDRS_KEY])

    if log_retention_days is not None:
        await _set(session, config, policy.RETENTION_KEY, str(logging_inputs["control_plane_log_retention_days"]))
    if log_types is not None:
        await _set(session, config, policy.LOG_TYPES_KEY, logging_inputs["control_plane_log_types"])
