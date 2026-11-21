"""Phase 19 - Stack deployment API endpoints.

- POST /api/v1/infrastructure/stack/deploy      - SSE stream for stack deploy
- POST /api/v1/infrastructure/stack/teardown     - SSE stream for stack teardown
- GET  /api/v1/infrastructure/stack/status       - current stack status
- GET  /api/v1/infrastructure/stack/components   - component list for active stack
- POST /api/v1/infrastructure/stack/components/{key}/toggle - enable/disable component
- GET  /api/v1/infrastructure/cluster/config     - current cluster config
- POST /api/v1/infrastructure/cluster/config     - update cluster config (generates plan)
- POST /api/v1/infrastructure/stack/sync-compute-config - re-read compute TF outputs
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from typing import Annotated, AsyncGenerator, Literal

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.api.streaming_responses import ClosingStreamingResponse
from app.api.terraform_streaming import client_event
from app.database import async_session_factory, get_session
from app.exceptions import DomainError, NotFoundError, ValidationError
from app.platform.cloud_provider import get_cloud_provider
from app.platform.platform_config_service import PlatformConfigService
from app.services.cluster_quota_service import ClusterQuotaService
from app.services.component_service import KUBERNETES_COMPONENTS, ComponentService
from app.services.terraform_cloud import AwsTerraformCloud, GcpTerraformCloud, get_terraform_cloud
from app.services.notebook_image_service import cancel_build
from app.services import infra_update_service
from app.services import infrastructure_operations as ops
from app.services.stack_deployment import (
    StackStatus,
    deploy_stack,
    destroy_storage,
    get_cluster_status,
    sync_compute_config,
    sync_storage_config,
    teardown_stack,
)
from app.services.storage_generation import UNRESOLVED_PARTIAL_MESSAGE
from app.services.terraform_executor import TerraformExecutor, TerraformProgressEvent
from app.services.terraform_stream import owned_event_stream
from app import runtime

logger = logging.getLogger("bioaf.stack_deploy_api")

# Curated user-facing messages produced by stack_deployment as ValueErrors.
# Anything outside this set is treated as an unexpected error and the user
# sees a generic message instead of the raw exception text.
_KNOWN_STACK_ERROR_MESSAGES = frozenset(
    {
        # The cloud-specific "not configured" message (GCP credentials flag /
        # AWS account identity), kept in sync with the TerraformCloud seam.
        GcpTerraformCloud().not_configured_message(),
        AwsTerraformCloud().not_configured_message(),
        "Terraform has not been initialized",
        "Compute stack is already deployed. Teardown first.",
        "Compute stack is not deployed",
        "Cannot destroy storage while compute stack is deployed. Teardown compute first.",
        "Storage is not deployed.",
        UNRESOLVED_PARTIAL_MESSAGE,
    }
)


def _safe_stack_error_message(exc: DomainError) -> str:
    msg = str(exc) if exc.args else ""
    # Return the matched allowlist constant rather than the exception-derived
    # `msg` so CodeQL's taint tracker sees the output as untainted. Behaviour
    # is identical: when `msg == known`, returning either is the same string.
    for known in _KNOWN_STACK_ERROR_MESSAGES:
        if msg == known:
            return known
    return "Stack operation failed. See server logs."


async def _stack_event_stream(
    session: AsyncSession,
    events: AsyncGenerator[TerraformProgressEvent, None],
) -> AsyncGenerator[str, None]:
    """Serve a stack operation over SSE as the owner of ``session``."""
    async with owned_event_stream(session, events) as stream:
        try:
            async for raw_event in stream:
                event = client_event(raw_event)
                payload: dict = {
                    "event_type": event.event_type,
                    "message": event.message,
                    "resource_address": event.resource_address,
                    "resources_completed": event.resources_completed,
                    "resources_total": event.resources_total,
                }
                if event.extra:
                    payload["extra"] = event.extra
                yield f"data: {json.dumps(payload)}\n\n"
        except DomainError as exc:
            logger.exception("Stack SSE stream failed")
            error_data = json.dumps({"event_type": "stack_error", "message": _safe_stack_error_message(exc)})
            yield f"data: {error_data}\n\n"


def _stack_sse_response(
    session: AsyncSession,
    events: AsyncGenerator[TerraformProgressEvent, None],
) -> ClosingStreamingResponse:
    return ClosingStreamingResponse(
        _stack_event_stream(session, events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


router = APIRouter(tags=["stack_deploy"])

# Repeat clicks and network retries of one confirmed action send the same key (ADR-050).
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


async def _accept(current_user: dict, action: str, key: str | None, inputs: dict | None = None) -> tuple[int, bool]:
    return await ops.accept(
        action=action, principal=ops.Principal.from_user(current_user), idempotency_key=key, inputs=inputs
    )


def _operation_sse(session: AsyncSession, operation_id: int, events) -> ClosingStreamingResponse:
    """Serve a stack generator over SSE as the accepted operation."""
    return _stack_sse_response(session, ops.operation_stream(operation_id, events))


def _spawn_stack(operation_id: int, make_events, role: str) -> None:
    ops.spawn_operation(operation_id, lambda handle: ops.drive_stack(handle, make_events), role=role)


# -----------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------


class StackDeployRequest(BaseModel):
    stack_type: str = "kubernetes"
    compute_region: str | None = None
    compute_zone: str | None = None


class StackTeardownRequest(BaseModel):
    confirm: bool = True


class StorageDestroyRequest(BaseModel):
    confirm: bool = False


class QuotaVerdictResponse(BaseModel):
    """What the region's quota allows for a pool shape.

    `status` is one of ok / warn / block / unverified. `block` means the pool
    could not create a single node, which is the state that produced a RUNNING
    node pool, a successful terraform apply, and zero schedulable capacity.
    """

    status: str
    achievable_nodes: int | None = None
    binding_metric: str | None = None
    message: str = ""


class ClusterConfigResponse(BaseModel):
    k8s_pipeline_machine_type: str
    k8s_pipeline_max_nodes: int
    k8s_pipeline_use_spot: bool
    k8s_pipeline_disk_size_gb: int
    k8s_pipeline_disk_type: str
    k8s_interactive_machine_type: str
    k8s_interactive_max_nodes: int
    # What the CURRENT pipeline pool can actually build. Present so an operator
    # sees an unbuildable pool on the page rather than discovering it hours later
    # when a run's pods sit Pending forever.
    pipeline_pool_quota: QuotaVerdictResponse | None = None


class ClusterConfigUpdate(BaseModel):
    k8s_pipeline_machine_type: str | None = None
    k8s_pipeline_max_nodes: int | None = None
    k8s_pipeline_use_spot: bool | None = None
    # Bounded like the work-node equivalent (`WorkNodeConfig.boot_disk_gb`), which has carried the
    # same control for standalone VMs all along. Below ~50 GB a node cannot hold a container image
    # and a work dir at once; the ceiling is GCE's.
    k8s_pipeline_disk_size_gb: int | None = Field(default=None, ge=50, le=1000)
    k8s_pipeline_disk_type: Literal["pd-standard", "pd-balanced", "pd-ssd"] | None = None
    k8s_interactive_machine_type: str | None = None
    k8s_interactive_max_nodes: int | None = None


class ComponentInfo(BaseModel):
    key: str
    name: str
    category: str
    description: str
    cost_estimate: str
    dependencies: list[str]
    status: str  # "enabled", "disabled", "coming_soon"
    configurable: bool


class ComponentListResponse(BaseModel):
    compute_stack: str | None
    compute_deployed: bool
    storage_deployed: bool
    components: list[ComponentInfo]


class ComponentToggleResponse(BaseModel):
    component_key: str
    enabled: bool
    status: str


class DeployProgressResponse(BaseModel):
    active: bool
    status: str | None = None
    phase: str | None = None
    resources_completed: int = 0
    resources_total: int = 0
    completed_resources: list[str] = []
    planned_resources: list[str] = []
    error_message: str | None = None
    run_id: int | None = None


class NotebookImageBuildStatus(BaseModel):
    build_id: str | None
    build_status: str | None
    image_uri: str | None


# -----------------------------------------------------------------------
# Stack deploy / teardown / status endpoints
# -----------------------------------------------------------------------


@router.post("/api/v1/infrastructure/stack/deploy")
async def stack_deploy_endpoint(
    body: StackDeployRequest | None = None,
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
):
    """Deploy the full compute stack via SSE stream."""
    runtime.require_admission("deployment")
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None
    stack_type = body.stack_type if body else "kubernetes"

    op_id, _ = await _accept(current_user, "compute_deploy", idempotency_key, {"stack_type": stack_type})
    return _operation_sse(session, op_id, deploy_stack(session, stack_type, user_id, org_id=org_id))


@router.post("/api/v1/infrastructure/stack/deploy-background")
async def stack_deploy_background_endpoint(
    body: StackDeployRequest | None = None,
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
):
    """Start a stack deploy in the background and return immediately.

    Used by the setup wizard to kick off deployment without maintaining
    an SSE connection. The DeploymentBanner polls terraform status to
    track progress.
    """
    runtime.require_admission("deployment")
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None
    stack_type = body.stack_type if body else "kubernetes"
    compute_region = body.compute_region if body else None
    compute_zone = body.compute_zone if body else None

    # Validate preconditions synchronously so we can return a clear error.
    # Cloud-aware: the "configured" gate differs per cloud (GCP credentials flag
    # vs AWS account identity), resolved through the TerraformCloud seam.
    cloud = get_terraform_cloud(await get_cloud_provider(session))
    cloud_config = await PlatformConfigService.get_many(session, cloud.config_keys())
    if not cloud.is_configured(cloud_config):
        raise HTTPException(status_code=400, detail=cloud.not_configured_message())

    tf_val = await PlatformConfigService.get(session, "terraform_initialized")
    if tf_val != "true":
        raise HTTPException(status_code=400, detail="Terraform has not been initialized")

    op_id, replayed = await _accept(
        current_user,
        "compute_deploy",
        idempotency_key,
        {"stack_type": stack_type, "compute_region": compute_region, "compute_zone": compute_zone},
    )
    if replayed:
        return {"message": "Deployment started", "operation_id": op_id}

    # Record the chosen compute_stack now so downstream views (the wizard's
    # Select Components step, the post-install components page) can resolve
    # the right component list before the compute module finishes applying.
    # Written after acceptance: no configuration changes before the operation owns it.
    await PlatformConfigService.set(session, "compute_stack", stack_type)
    await session.commit()

    _spawn_stack(
        op_id,
        lambda bg_session: deploy_stack(
            bg_session,
            stack_type,
            user_id,
            org_id=org_id,
            compute_region=compute_region,
            compute_zone=compute_zone,
        ),
        role="stack-deploy",
    )
    return {"message": "Deployment started", "operation_id": op_id}


@router.post("/api/v1/infrastructure/stack/teardown-background")
async def stack_teardown_background_endpoint(
    body: StackTeardownRequest | None = None,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
):
    """Start a stack teardown in the background and return immediately."""
    runtime.require_admission("deployment")
    if body and not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required. Set confirm=true.")

    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None

    # Validate compute is deployed
    compute_deployed = await PlatformConfigService.get(session, "compute_deployed")
    if compute_deployed != "true":
        raise HTTPException(status_code=400, detail="Compute stack is not deployed")

    op_id, replayed = await _accept(current_user, "compute_teardown", idempotency_key)
    if not replayed:
        _spawn_stack(
            op_id, lambda bg_session: teardown_stack(bg_session, user_id, org_id=org_id), role="stack-teardown"
        )
    return {"message": "Teardown started", "operation_id": op_id}


@router.post("/api/v1/infrastructure/stack/destroy-storage-background")
async def stack_destroy_storage_background_endpoint(
    body: StorageDestroyRequest | None = None,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
):
    """Start storage destruction in the background and return immediately."""
    runtime.require_admission("deployment")
    if body is not None and not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required. Set confirm=true.")

    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None

    # Validate storage is deployed and compute is not
    compute_deployed = await PlatformConfigService.get(session, "compute_deployed")
    if compute_deployed == "true":
        raise HTTPException(status_code=400, detail="Teardown compute stack before destroying storage")

    storage_deployed = await PlatformConfigService.get(session, "storage_deployed")
    if storage_deployed != "true":
        raise HTTPException(status_code=400, detail="Storage is not deployed")

    op_id, replayed = await _accept(current_user, "storage_destroy", idempotency_key)
    if not replayed:
        _spawn_stack(
            op_id,
            lambda bg_session: destroy_storage(bg_session, user_id, org_id=org_id),
            role="stack-destroy-storage",
        )
    return {"message": "Storage destruction started", "operation_id": op_id}


@router.get(
    "/api/v1/infrastructure/stack/deploy/progress",
    response_model=DeployProgressResponse,
)
async def stack_deploy_progress(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
) -> DeployProgressResponse:
    """Poll deployment progress. Returns the most recent active run or idle state."""
    from datetime import datetime, timedelta, timezone

    from app.models.component import TerraformRun
    from sqlalchemy import and_, or_, select

    # Include active runs, plus recently completed/failed runs so the
    # frontend can display the terminal state before dismissing.
    recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)

    result = await session.execute(
        select(TerraformRun)
        .where(
            or_(
                TerraformRun.status.in_(["planning", "applying", "awaiting_confirmation"]),
                and_(
                    TerraformRun.status.in_(["failed", "completed"]),
                    TerraformRun.completed_at >= recent_cutoff,
                ),
            )
        )
        .order_by(TerraformRun.started_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()

    if run is None:
        return DeployProgressResponse(active=False)

    is_active = run.status in ("planning", "applying", "awaiting_confirmation")

    # Only include planned/completed resource lists for active runs.
    # Stale data from previous runs confuses the frontend.
    planned = []
    completed = []
    if is_active:
        if run.plan_json and run.plan_json.get("resources"):
            planned = [r["address"] for r in run.plan_json["resources"] if not r["address"].startswith("data.")]
        completed = run.completed_resources or []

    return DeployProgressResponse(
        active=is_active,
        status=run.status,
        phase=run.deploy_phase,
        resources_completed=run.resources_completed if is_active else 0,
        resources_total=(run.resources_planned or 0) if is_active else 0,
        completed_resources=completed,
        planned_resources=planned,
        error_message=run.error_message,
        run_id=run.id,
    )


@router.post("/api/v1/infrastructure/stack/teardown")
async def stack_teardown_endpoint(
    body: StackTeardownRequest | None = None,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
):
    """Teardown the compute stack via SSE stream."""
    runtime.require_admission("deployment")
    if body and not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required. Set confirm=true.")

    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None

    op_id, _ = await _accept(current_user, "compute_teardown", idempotency_key)
    return _operation_sse(session, op_id, teardown_stack(session, user_id, org_id=org_id))


@router.post("/api/v1/infrastructure/stack/destroy-storage")
async def stack_destroy_storage_endpoint(
    body: StorageDestroyRequest | None = None,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
):
    """Destroy the storage module (GCS buckets + Pub/Sub) via SSE stream.

    Only allowed when compute stack is torn down. A confirmed destroy retires
    the storage generation so the next deploy creates new bucket names.
    """
    # A missing body is treated as confirmed -- the UI enforces the 3-step
    # confirmation (warning, checkbox, typed phrase) before calling this endpoint.
    runtime.require_admission("deployment")
    if body is not None and not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required. Set confirm=true.")

    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None

    op_id, _ = await _accept(current_user, "storage_destroy", idempotency_key)
    return _operation_sse(session, op_id, destroy_storage(session, user_id, org_id=org_id))


@router.post("/api/v1/infrastructure/stack/sync-storage-config")
async def sync_storage_config_endpoint(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-read Terraform storage outputs and write bucket names to platform_config.

    Use this after a deployment where storage was applied before the automatic
    output-persistence was in place (i.e. bucket names are missing from
    platform_config but the GCS buckets exist).
    """
    op_id, _ = await _accept(current_user, "config_sync", None, {"module": "storage"})
    try:
        async with ops.owned(op_id):
            populated = await sync_storage_config(session)
            await session.commit()
        await ops.finish(op_id, ops.succeeded())
        return {"status": "ok", "populated": populated, "operation_id": op_id}
    except DomainError:
        raise
    except Exception as exc:
        logger.error("Storage config sync failed (error_type=%s)", type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=500, detail="Storage config sync failed")


@router.post("/api/v1/infrastructure/stack/sync-compute-config")
async def sync_compute_config_endpoint(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-read Terraform compute outputs and write cluster config to platform_config.

    Use this after a deployment where the terraform output capture failed,
    leaving gke_cluster_endpoint as 'null' in platform_config.
    """
    op_id, _ = await _accept(current_user, "config_sync", None, {"module": "compute"})
    try:
        async with ops.owned(op_id):
            populated = await sync_compute_config(session)
            await session.commit()
        await ops.finish(op_id, ops.succeeded())
        # Force the compute adapter to reload cluster config from DB
        try:
            from app.adapters.registry import get_compute_adapter

            adapter = get_compute_adapter()
            if hasattr(adapter, "load_cluster_config"):
                await adapter.load_cluster_config(force=True)
        except Exception:
            pass  # Adapter may not be initialized yet
        return {"status": "ok", "populated": populated, "operation_id": op_id}
    except DomainError:
        raise
    except Exception as exc:
        logger.error("Compute config sync failed (error_type=%s)", type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=500, detail="Compute config sync failed")


@router.get("/api/v1/infrastructure/stack/status")
async def stack_status_endpoint(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> StackStatus:
    """Return current stack and cluster status."""
    return await get_cluster_status(session)


# -----------------------------------------------------------------------
# Infrastructure updates (re-plan deployed modules, apply additive changes)
# -----------------------------------------------------------------------


class ApplyUpdatesRequest(BaseModel):
    modules: list[str]


class CheckUpdatesAccepted(BaseModel):
    message: str
    operation_id: int


@router.post("/api/v1/infrastructure/stack/check-updates", response_model=CheckUpdatesAccepted)
async def check_infra_updates_endpoint(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
) -> CheckUpdatesAccepted:
    """Start the confirmed "Check for infrastructure updates" action (mockup M1).

    The popup confirmation is the authorization: one accepted operation plans every deployed
    module and applies the additive changes automatically, with no second approval. Changes that
    would delete or replace an existing resource are listed in the operation's outcome and never
    applied. Progress and outcome are read from the operation.
    """
    runtime.require_admission("deployment")
    user_id = int(current_user["sub"])
    # Refuse early what cannot run at all, before taking exclusion.
    try:
        await infra_update_service._deployed_modules(session)
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    op_id, replayed = await _accept(current_user, "infrastructure_update", idempotency_key)
    if not replayed:
        infra_update_service.launch_update_check(user_id, operation_id=op_id)
    return CheckUpdatesAccepted(message="Infrastructure update started", operation_id=op_id)


@router.post("/api/v1/infrastructure/stack/apply-updates")
async def apply_infra_updates_endpoint(
    body: ApplyUpdatesRequest,
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
) -> dict:
    """Apply the named modules in the background, additive-only.

    Used after the user reviews a plan that also contains destructive changes:
    each module is re-planned and only its create/update resources are applied
    (targeted), so a delete/replace is never carried out by this flow."""
    runtime.require_admission("deployment")
    user_id = int(current_user["sub"])
    # The server owns the scope: only known modules that are actually deployed.
    try:
        deployed = set(await infra_update_service._deployed_modules(session))
    except DomainError:
        deployed = set()
    modules = [m for m in body.modules if m in deployed]
    if not modules:
        raise HTTPException(status_code=400, detail="No valid modules to apply")
    op_id, replayed = await _accept(current_user, "infrastructure_update", idempotency_key, {"modules": modules})
    if not replayed:
        infra_update_service.launch_background_apply(modules, user_id, operation_id=op_id)
    return {"applying": True, "modules": modules, "operation_id": op_id}


# -----------------------------------------------------------------------
# Components list and toggle
# -----------------------------------------------------------------------


@router.get("/api/v1/infrastructure/stack/components")
async def stack_components_list(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> ComponentListResponse:
    """Return component list based on active compute stack."""
    config = await PlatformConfigService.get_many(session, ["compute_stack", "compute_deployed", "storage_deployed"])

    compute_stack = config.get("compute_stack", "null")
    if compute_stack == "null":
        compute_stack = None
    compute_deployed = config.get("compute_deployed", "false") == "true"
    storage_deployed = config.get("storage_deployed", "false") == "true"

    if compute_stack is None:
        return ComponentListResponse(
            compute_stack=None,
            compute_deployed=False,
            storage_deployed=storage_deployed,
            components=[],
        )

    # Get current component states from DB
    state_rows = (await session.execute(text("SELECT component_key, enabled, status FROM component_states"))).fetchall()
    state_map = {r[0]: {"enabled": r[1], "status": r[2]} for r in state_rows}

    # Components with no backend implementation yet
    unimplemented = {"snakemake"}

    components = []
    for comp_def in KUBERNETES_COMPONENTS:
        if comp_def["key"] in unimplemented:
            status = "coming_soon"
        else:
            state = state_map.get(comp_def["key"], {"enabled": False, "status": "disabled"})
            if state["enabled"]:
                # Preserve provisioning/build_failed status from component_states
                if state["status"] in ("provisioning", "build_failed"):
                    status = state["status"]
                else:
                    status = "enabled"
            else:
                status = "disabled"

        components.append(
            ComponentInfo(
                key=comp_def["key"],
                name=comp_def["name"],
                category=comp_def["category"],
                description=comp_def["description"],
                cost_estimate=comp_def["cost_estimate"],
                dependencies=comp_def["dependencies"],
                status=status,
                configurable=comp_def["configurable"],
            )
        )

    return ComponentListResponse(
        compute_stack=compute_stack,
        compute_deployed=compute_deployed,
        storage_deployed=storage_deployed,
        components=components,
    )


@router.post("/api/v1/infrastructure/stack/components/{component_key}/toggle")
async def stack_component_toggle(
    component_key: str,
    force_rebuild: bool = False,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> ComponentToggleResponse:
    """Toggle a component's enabled state with dependency enforcement.

    The whole change is one infrastructure operation: it holds the shared lock from before the
    state is read until it commits, so no other action can start in between (M3/M6). An image
    build it starts belongs to it, and the lock is kept until the provider reports that build.
    """
    user_id = int(current_user["sub"])
    try:
        ComponentService.stack_component(component_key)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    operation_id, _ = await ops.accept(
        action="component_change",
        principal=ops.Principal(user_id),
        inputs={"component_key": component_key, "force_rebuild": force_rebuild},
    )
    try:
        async with ops.owned(operation_id):
            result = await ComponentService.toggle_stack_component(
                session,
                component_key,
                user_id=user_id,
                force_rebuild=force_rebuild,
            )
            await session.commit()
    except ValidationError as exc:
        # owned() recorded the failed outcome; the caller gets the reason.
        raise HTTPException(status_code=400, detail=str(exc))
    await ops.finish(
        operation_id, ops.succeeded(f"{result.component_key} {'enabled' if result.enabled else 'disabled'}.")
    )

    return ComponentToggleResponse(
        component_key=result.component_key,
        enabled=result.enabled,
        status=result.status,
    )


# -----------------------------------------------------------------------
# Notebook image build status
# -----------------------------------------------------------------------


@router.get("/api/v1/infrastructure/notebook-image/build-status")
async def notebook_image_build_status(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> NotebookImageBuildStatus:
    """Return current notebook image build status."""
    config = await PlatformConfigService.get_many(
        session, ["notebook_image_build_id", "notebook_image_build_status", "bioaf_scrna_image"]
    )

    def _non_null(val: str | None) -> str | None:
        return val if val and val != "null" else None

    return NotebookImageBuildStatus(
        build_id=_non_null(config.get("notebook_image_build_id")),
        build_status=_non_null(config.get("notebook_image_build_status")),
        image_uri=_non_null(config.get("bioaf_scrna_image")),
    )


@router.get("/api/v1/infrastructure/cellxgene-image/build-status")
async def cellxgene_image_build_status(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> NotebookImageBuildStatus:
    """Return current cellxgene image build status."""
    config = await PlatformConfigService.get_many(
        session, ["cellxgene_image_build_id", "cellxgene_image_build_status", "cellxgene_image"]
    )

    def _non_null(val: str | None) -> str | None:
        return val if val and val != "null" else None

    return NotebookImageBuildStatus(
        build_id=_non_null(config.get("cellxgene_image_build_id")),
        build_status=_non_null(config.get("cellxgene_image_build_status")),
        image_uri=_non_null(config.get("cellxgene_image")),
    )


@router.post("/api/v1/infrastructure/notebook-image/cancel")
async def notebook_image_cancel(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Request cancellation of the active notebook image build; the poller confirms it."""
    build_id = await cancel_build(session, int(current_user["sub"]))
    await session.commit()
    return {"cancelled": True, "build_id": build_id}


# -----------------------------------------------------------------------
# Cluster config
# -----------------------------------------------------------------------


@router.get("/api/v1/infrastructure/cluster/config")
async def get_cluster_config(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> ClusterConfigResponse:
    """Return current cluster configuration from platform_config."""
    keys = [
        "k8s_pipeline_machine_type",
        "k8s_pipeline_max_nodes",
        "k8s_pipeline_use_spot",
        "k8s_pipeline_disk_size_gb",
        "k8s_pipeline_disk_type",
        "k8s_interactive_machine_type",
        "k8s_interactive_max_nodes",
    ]
    config = await PlatformConfigService.get_many(session, keys)

    return ClusterConfigResponse(
        k8s_pipeline_machine_type=config.get("k8s_pipeline_machine_type", "n2-highmem-16"),
        k8s_pipeline_max_nodes=int(config.get("k8s_pipeline_max_nodes", "20")),
        k8s_pipeline_use_spot=config.get("k8s_pipeline_use_spot", "true") == "true",
        # Defaults mirror the terraform variables, so an install that never set them reports what
        # its pool actually runs rather than a zero.
        k8s_pipeline_disk_size_gb=int(config.get("k8s_pipeline_disk_size_gb", "100")),
        k8s_pipeline_disk_type=config.get("k8s_pipeline_disk_type", "pd-standard"),
        k8s_interactive_machine_type=config.get("k8s_interactive_machine_type", "e2-standard-8"),
        k8s_interactive_max_nodes=int(config.get("k8s_interactive_max_nodes", "5")),
        pipeline_pool_quota=_verdict_response(await ClusterQuotaService.evaluate_pipeline_pool(session)),
    )


def _verdict_response(verdict) -> QuotaVerdictResponse:
    return QuotaVerdictResponse(
        status=verdict.status,
        achievable_nodes=verdict.achievable_nodes,
        binding_metric=verdict.binding_metric,
        message=verdict.message,
    )


@router.post("/api/v1/infrastructure/cluster/config/preflight")
async def preflight_cluster_config(
    body: ClusterConfigUpdate,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> QuotaVerdictResponse:
    """Report what a proposed pipeline-pool config could actually build.

    Read-only: it mutates nothing and plans nothing. The Components page calls it
    as the operator edits, so a config that cannot create a node is refused at the
    form rather than discovered as a run that never starts.
    """
    verdict = await ClusterQuotaService.evaluate_pipeline_pool(session, proposed=body.model_dump(exclude_none=True))
    return _verdict_response(verdict)


@router.post("/api/v1/infrastructure/cluster/config")
async def update_cluster_config(
    body: ClusterConfigUpdate,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: IdempotencyKey = None,
) -> dict:
    """Update cluster config, plan, and auto-apply."""
    runtime.require_admission("deployment")
    from app.adapters.registry import require_capability

    require_capability("autoscaling")
    # Verify compute is deployed
    deployed = await PlatformConfigService.get(session, "compute_deployed")
    if deployed != "true":
        raise HTTPException(status_code=400, detail="Compute stack is not deployed")

    # Refuse a pool that could not create a single node. Only "block" refuses:
    # a pool that runs fewer nodes than requested still works, and an unreadable
    # quota must never stop an operator (a missing IAM role would otherwise make
    # this page unusable).
    updates = body.model_dump(exclude_none=True)
    verdict = await ClusterQuotaService.evaluate_pipeline_pool(session, proposed=updates)
    if verdict.status == "block":
        raise HTTPException(status_code=400, detail=verdict.message)

    # One operation from the configuration change through plan and apply; the
    # explicit configure action is the authorization.
    user_id = int(current_user["sub"])
    op_id, replayed = await _accept(current_user, "cluster_config", idempotency_key, {"updates": updates})
    if replayed:
        return {"message": "Cluster configuration update started", "operation_id": op_id}

    async with ops.owned(op_id) as handle:
        await handle.step("plan", "Planning changes", "running")
        for key, value in updates.items():
            await PlatformConfigService.set(session, key, str(value).lower() if isinstance(value, bool) else str(value))
        await session.flush()
        run = await TerraformExecutor.run_plan(session, user_id, module_name="compute")
        if run.status == "failed":
            await session.commit()
            await handle.step("plan", state="failed")
            await ops.finish(op_id, ops.failed("Planning the cluster change failed.", "plan_failed"))
            raise HTTPException(status_code=500, detail=run.error_message or "Terraform plan failed")
        # Auto-apply: transition to applying and continue the operation in the background.
        run.status = "applying"
        await session.commit()
        await handle.step("plan", state="done")

    ops.spawn_operation(
        op_id, lambda handle: _apply_cluster_change(handle, run.id, user_id), role="cluster-config-apply"
    )
    return {"message": "Cluster configuration update started", "run_id": run.id, "operation_id": op_id}


async def _apply_cluster_change(handle: ops.OperationHandle, run_id: int, user_id: int) -> ops.OperationResult:
    """Apply the planned cluster change as the operation's last phase."""
    from app.services.terraform_stream import INTERRUPTED_EVENT

    await handle.step("apply", "Applying cluster changes", "running")
    outcome = await _run_apply_background(run_id, user_id)
    if outcome.succeeded:
        return ops.succeeded("Cluster settings applied.")
    if outcome.terminal_event == INTERRUPTED_EVENT:
        return ops.OperationResult("interrupted", "The cluster change was interrupted.", "interrupted")
    return ops.failed("Applying the cluster change failed.", "apply_failed")


async def _run_apply_background(run_id: int, user_id: int):
    """Run TerraformExecutor.run_apply with its own session; return what its events established."""
    from app.services.terraform_stream import OperationOutcome

    outcome = OperationOutcome()
    async with async_session_factory() as bg_session:
        try:
            async with owned_event_stream(
                bg_session, TerraformExecutor.run_apply(bg_session, run_id, user_id)
            ) as events:
                async for event in events:
                    outcome.observe(event)
        except Exception:
            logger.exception("Background terraform apply failed for run %d", run_id)
    return outcome
