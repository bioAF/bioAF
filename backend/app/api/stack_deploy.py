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

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_role
from app.database import async_session_factory, get_session
from app.services.stack_deployment import (
    StackStatus,
    deploy_stack,
    destroy_storage,
    get_cluster_status,
    sync_compute_config,
    sync_storage_config,
    teardown_stack,
)
from app.services.terraform_executor import TerraformExecutor

logger = logging.getLogger("bioaf.stack_deploy_api")

router = APIRouter(tags=["stack_deploy"])


# -----------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------


class StackDeployRequest(BaseModel):
    stack_type: str = "kubernetes"


class StackTeardownRequest(BaseModel):
    confirm: bool = True


class StorageDestroyRequest(BaseModel):
    confirm: bool = False


class ClusterConfigResponse(BaseModel):
    k8s_pipeline_machine_type: str
    k8s_pipeline_max_nodes: int
    k8s_pipeline_use_spot: bool
    k8s_interactive_machine_type: str
    k8s_interactive_max_nodes: int


class ClusterConfigUpdate(BaseModel):
    k8s_pipeline_machine_type: str | None = None
    k8s_pipeline_max_nodes: int | None = None
    k8s_pipeline_use_spot: bool | None = None
    k8s_interactive_machine_type: str | None = None
    k8s_interactive_max_nodes: int | None = None


class ClusterConfigPlanResponse(BaseModel):
    run_id: int
    status: str
    plan_summary: dict | None = None


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


# -----------------------------------------------------------------------
# Component catalog for the stack-based view
# -----------------------------------------------------------------------

KUBERNETES_COMPONENTS: list[dict] = [
    {
        "key": "nextflow",
        "name": "Nextflow",
        "category": "pipeline_orchestration",
        "description": "Pipeline orchestration using Nextflow with native Kubernetes executor. Supports nf-core workflows.",
        "cost_estimate": "$0 (uses Kubernetes compute)",
        "dependencies": ["kubernetes_cluster"],
        "configurable": False,
    },
    {
        "key": "snakemake",
        "name": "Snakemake",
        "category": "pipeline_orchestration",
        "description": "Pipeline orchestration using Snakemake with Kubernetes executor support.",
        "cost_estimate": "$0 (uses Kubernetes compute)",
        "dependencies": ["kubernetes_cluster"],
        "configurable": False,
    },
    {
        "key": "jupyterhub",
        "name": "JupyterHub",
        "category": "analysis",
        "description": "Managed Jupyter notebook environment on Kubernetes with pre-built scRNA-seq kernels.",
        "cost_estimate": "$50-$200/month",
        "dependencies": ["kubernetes_cluster"],
        "configurable": True,
    },
    {
        "key": "rstudio",
        "name": "RStudio",
        "category": "analysis",
        "description": "Managed RStudio environment on Kubernetes with Seurat and Bioconductor pre-installed.",
        "cost_estimate": "$50-$200/month",
        "dependencies": ["kubernetes_cluster"],
        "configurable": True,
    },
    {
        "key": "cellxgene",
        "name": "cellxgene",
        "category": "visualization",
        "description": "Interactive single-cell data explorer for h5ad files.",
        "cost_estimate": "$20-$50/month",
        "dependencies": [],
        "configurable": False,
    },
    {
        "key": "qc_dashboard",
        "name": "QC Dashboard",
        "category": "visualization",
        "description": "Auto-generated quality control dashboards after pipeline runs.",
        "cost_estimate": "$10-$30/month",
        "dependencies": ["nextflow"],
        "configurable": False,
    },
    {
        "key": "meilisearch",
        "name": "Meilisearch",
        "category": "search",
        "description": "Full-text search over protocols, metadata, and pipeline logs.",
        "cost_estimate": "$20-$50/month",
        "dependencies": [],
        "configurable": False,
    },
]


# -----------------------------------------------------------------------
# Stack deploy / teardown / status endpoints
# -----------------------------------------------------------------------


@router.post("/api/v1/infrastructure/stack/deploy")
async def stack_deploy_endpoint(
    body: StackDeployRequest | None = None,
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
):
    """Deploy the full compute stack via SSE stream."""
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None
    stack_type = body.stack_type if body else "kubernetes"

    async def event_generator():
        try:
            async for event in deploy_stack(session, stack_type, user_id, org_id=org_id):
                data = json.dumps(
                    {
                        "event_type": event.event_type,
                        "message": event.message,
                        "resource_address": event.resource_address,
                        "resources_completed": event.resources_completed,
                        "resources_total": event.resources_total,
                    }
                )
                yield f"data: {data}\n\n"
        except ValueError as exc:
            error_data = json.dumps({"event_type": "stack_error", "message": str(exc)})
            yield f"data: {error_data}\n\n"
        finally:
            await session.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/v1/infrastructure/stack/deploy-background")
async def stack_deploy_background_endpoint(
    body: StackDeployRequest | None = None,
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
):
    """Start a stack deploy in the background and return immediately.

    Used by the setup wizard to kick off deployment without maintaining
    an SSE connection. The DeploymentBanner polls terraform status to
    track progress.
    """
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None
    stack_type = body.stack_type if body else "kubernetes"

    # Validate preconditions synchronously so we can return a clear error.
    gcp_configured = await session.execute(
        text("SELECT value FROM platform_config WHERE key = 'gcp_credentials_configured'")
    )
    gcp_val = gcp_configured.scalar_one_or_none()
    if gcp_val != "true":
        raise HTTPException(status_code=400, detail="GCP credentials are not configured")

    tf_initialized = await session.execute(
        text("SELECT value FROM platform_config WHERE key = 'terraform_initialized'")
    )
    tf_val = tf_initialized.scalar_one_or_none()
    if tf_val != "true":
        raise HTTPException(status_code=400, detail="Terraform has not been initialized")

    async def _run_deploy():
        """Drain the deploy_stack generator in the background with its own session."""
        async with async_session_factory() as bg_session:
            try:
                async for _event in deploy_stack(bg_session, stack_type, user_id, org_id=org_id):
                    pass  # Events are consumed; terraform runs as a subprocess
                await bg_session.commit()
            except Exception:
                logger.exception("Background deploy failed")
                await bg_session.rollback()

    asyncio.get_event_loop().create_task(_run_deploy())

    return {"message": "Deployment started"}


@router.post("/api/v1/infrastructure/stack/teardown")
async def stack_teardown_endpoint(
    body: StackTeardownRequest | None = None,
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
):
    """Teardown the compute stack via SSE stream."""
    if body and not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required. Set confirm=true.")

    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None

    async def event_generator():
        try:
            async for event in teardown_stack(session, user_id, org_id=org_id):
                data = json.dumps(
                    {
                        "event_type": event.event_type,
                        "message": event.message,
                    }
                )
                yield f"data: {data}\n\n"
        except ValueError as exc:
            error_data = json.dumps({"event_type": "stack_error", "message": str(exc)})
            yield f"data: {error_data}\n\n"
        finally:
            await session.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/v1/infrastructure/stack/destroy-storage")
async def stack_destroy_storage_endpoint(
    body: StorageDestroyRequest | None = None,
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
):
    """Destroy the storage module (GCS buckets + Pub/Sub) via SSE stream.

    Only allowed when compute stack is torn down. Resets stack_uid so the
    next deploy creates fresh bucket names (avoids GCS soft-delete conflicts).
    """
    # A missing body is treated as confirmed -- the UI enforces the 3-step
    # confirmation (warning, checkbox, typed phrase) before calling this endpoint.
    if body is not None and not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required. Set confirm=true.")

    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"]) if current_user.get("org_id") else None

    async def event_generator():
        try:
            async for event in destroy_storage(session, user_id, org_id=org_id):
                data = json.dumps(
                    {
                        "event_type": event.event_type,
                        "message": event.message,
                        "resource_address": event.resource_address,
                        "resources_completed": event.resources_completed,
                        "resources_total": event.resources_total,
                    }
                )
                yield f"data: {data}\n\n"
        except ValueError as exc:
            error_data = json.dumps({"event_type": "stack_error", "message": str(exc)})
            yield f"data: {error_data}\n\n"
        finally:
            await session.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/v1/infrastructure/stack/sync-storage-config")
async def sync_storage_config_endpoint(
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-read Terraform storage outputs and write bucket names to platform_config.

    Use this after a deployment where storage was applied before the automatic
    output-persistence was in place (i.e. bucket names are missing from
    platform_config but the GCS buckets exist).
    """
    try:
        populated = await sync_storage_config(session)
        await session.commit()
        return {"status": "ok", "populated": populated}
    except Exception as exc:
        logger.error("Storage config sync failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/v1/infrastructure/stack/sync-compute-config")
async def sync_compute_config_endpoint(
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-read Terraform compute outputs and write cluster config to platform_config.

    Use this after a deployment where the terraform output capture failed,
    leaving gke_cluster_endpoint as 'null' in platform_config.
    """
    try:
        populated = await sync_compute_config(session)
        await session.commit()
        # Force the compute adapter to reload cluster config from DB
        try:
            from app.adapters.registry import get_compute_adapter

            adapter = get_compute_adapter()
            if hasattr(adapter, "load_cluster_config"):
                await adapter.load_cluster_config(force=True)
        except Exception:
            pass  # Adapter may not be initialized yet
        return {"status": "ok", "populated": populated}
    except Exception as exc:
        logger.error("Compute config sync failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/v1/infrastructure/stack/status")
async def stack_status_endpoint(
    current_user: dict = require_role("admin", "comp_bio"),
    session: AsyncSession = Depends(get_session),
) -> StackStatus:
    """Return current stack and cluster status."""
    return await get_cluster_status(session)


# -----------------------------------------------------------------------
# Components list and toggle
# -----------------------------------------------------------------------


@router.get("/api/v1/infrastructure/stack/components")
async def stack_components_list(
    current_user: dict = require_role("admin", "comp_bio"),
    session: AsyncSession = Depends(get_session),
) -> ComponentListResponse:
    """Return component list based on active compute stack."""
    rows = (
        await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('compute_stack', 'compute_deployed', 'storage_deployed')"
            )
        )
    ).fetchall()
    config = {r[0]: r[1] for r in rows}

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

    components = []
    for comp_def in KUBERNETES_COMPONENTS:
        state = state_map.get(comp_def["key"], {"enabled": False, "status": "disabled"})
        if state["enabled"]:
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
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> ComponentToggleResponse:
    """Toggle a component's enabled state with dependency enforcement."""
    # Block toggling kubernetes_cluster directly (use stack teardown)
    if component_key == "kubernetes_cluster":
        raise HTTPException(
            status_code=400,
            detail="Cannot toggle kubernetes_cluster directly. Use stack deploy/teardown.",
        )

    # Find component definition
    comp_def = None
    for c in KUBERNETES_COMPONENTS:
        if c["key"] == component_key:
            comp_def = c
            break

    if comp_def is None:
        raise HTTPException(status_code=404, detail=f"Component '{component_key}' not found")

    # Get current state
    row = (
        await session.execute(
            text("SELECT enabled, status FROM component_states WHERE component_key = :key").bindparams(
                key=component_key
            )
        )
    ).fetchone()

    currently_enabled = row[0] if row else False

    if not currently_enabled:
        # Enabling: check dependencies
        if comp_def["dependencies"]:
            dep_rows = (
                await session.execute(
                    text(
                        "SELECT component_key, enabled FROM component_states WHERE component_key = ANY(:keys)"
                    ).bindparams(keys=comp_def["dependencies"])
                )
            ).fetchall()
            enabled_deps = {r[0] for r in dep_rows if r[1]}
            missing = [d for d in comp_def["dependencies"] if d not in enabled_deps]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Dependency not met: {', '.join(missing)} must be enabled first",
                )

        # Enable
        await session.execute(
            text("""
            UPDATE component_states SET enabled = true, status = 'enabled'
            WHERE component_key = :key
            """).bindparams(key=component_key)
        )
        new_enabled = True
        new_status = "enabled"
    else:
        # Disable
        await session.execute(
            text("""
            UPDATE component_states SET enabled = false, status = 'disabled'
            WHERE component_key = :key
            """).bindparams(key=component_key)
        )
        new_enabled = False
        new_status = "disabled"

    await session.commit()

    return ComponentToggleResponse(
        component_key=component_key,
        enabled=new_enabled,
        status=new_status,
    )


# -----------------------------------------------------------------------
# Cluster config
# -----------------------------------------------------------------------


@router.get("/api/v1/infrastructure/cluster/config")
async def get_cluster_config(
    current_user: dict = require_role("admin", "comp_bio"),
    session: AsyncSession = Depends(get_session),
) -> ClusterConfigResponse:
    """Return current cluster configuration from platform_config."""
    keys = [
        "k8s_pipeline_machine_type",
        "k8s_pipeline_max_nodes",
        "k8s_pipeline_use_spot",
        "k8s_interactive_machine_type",
        "k8s_interactive_max_nodes",
    ]
    rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys)
        )
    ).fetchall()
    config = {r[0]: r[1] for r in rows}

    return ClusterConfigResponse(
        k8s_pipeline_machine_type=config.get("k8s_pipeline_machine_type", "n2-highmem-8"),
        k8s_pipeline_max_nodes=int(config.get("k8s_pipeline_max_nodes", "20")),
        k8s_pipeline_use_spot=config.get("k8s_pipeline_use_spot", "true") == "true",
        k8s_interactive_machine_type=config.get("k8s_interactive_machine_type", "n2-standard-4"),
        k8s_interactive_max_nodes=int(config.get("k8s_interactive_max_nodes", "5")),
    )


@router.post("/api/v1/infrastructure/cluster/config")
async def update_cluster_config(
    body: ClusterConfigUpdate,
    current_user: dict = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> ClusterConfigPlanResponse:
    """Update cluster config by generating a Terraform plan."""
    # Verify compute is deployed
    deployed = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'compute_deployed'"))
    ).fetchone()
    if not deployed or deployed[0] != "true":
        raise HTTPException(status_code=400, detail="Compute stack is not deployed")

    # Update config values
    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        await session.execute(
            text("UPDATE platform_config SET value = :v, updated_at = now() WHERE key = :k").bindparams(
                k=key, v=str(value).lower() if isinstance(value, bool) else str(value)
            )
        )
    await session.flush()

    # Generate terraform plan
    user_id = int(current_user["sub"])
    run = await TerraformExecutor.run_plan(session, user_id, module_name="compute")
    await session.commit()

    # Build plan_summary from plan_json (run_plan writes to plan_json, not plan_summary_json)
    plan_summary = None
    if run.plan_json:
        pj = run.plan_json
        add_list = [r for r in pj.get("resources", []) if r.get("action") == "create"]
        change_list = [r for r in pj.get("resources", []) if r.get("action") == "update"]
        destroy_list = [r for r in pj.get("resources", []) if r.get("action") == "delete"]
        replace_list = [r for r in pj.get("resources", []) if r.get("action") == "replace"]
        plan_summary = {
            "add": add_list + replace_list,
            "change": change_list,
            "destroy": destroy_list + replace_list,
            "add_count": pj.get("add_count", 0),
            "change_count": pj.get("change_count", 0),
            "destroy_count": pj.get("destroy_count", 0),
        }

    return ClusterConfigPlanResponse(
        run_id=run.id,
        status=run.status,
        plan_summary=plan_summary,
    )
