from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.component import (
    ComponentListResponse,
    ComponentSelectBatchRequest,
    ComponentSelectBatchResponse,
    ComponentStateResponse,
)
from app.platform.platform_config_service import PlatformConfigService
from app.services.component_service import COMPONENT_CATALOG, ComponentService
from app.services import role_service

router = APIRouter(prefix="/api/components", tags=["components"])


async def _require_admin(request: Request, session: AsyncSession) -> dict:
    current_user = request.state.current_user
    if not await role_service.has_permission(session, int(current_user["role_id"]), "infrastructure", "configure"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _build_response(key: str, state) -> ComponentStateResponse:
    catalog = COMPONENT_CATALOG.get(key, {})
    return ComponentStateResponse(
        key=key,
        name=catalog.get("name", key),
        description=catalog.get("description", ""),
        category=catalog.get("category", ""),
        enabled=state.enabled if state else False,
        status=state.status if state else "disabled",
        config=dict(state.config_json) if state and state.config_json else {},
        dependencies=catalog.get("dependencies", []),
        estimated_monthly_cost=catalog.get("estimated_monthly_cost", ""),
        updated_at=state.updated_at if state else None,
    )


@router.post("/select-batch", response_model=ComponentSelectBatchResponse)
async def select_batch(
    body: ComponentSelectBatchRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Queue a set of components for the post-deploy orchestrator.

    Called by the setup wizard's "Select Components" step. Each accepted key
    becomes a component_states row with enabled=true, status='queued_for_infra'.
    The drain orchestrator (process_queued_components) takes it from there as
    infra readiness flips.

    Validation is all-or-nothing: any unknown key, or any key that does not
    belong to the active compute_stack, fails the whole batch and no rows
    are written.
    """
    await _require_admin(request, session)

    keys = list(dict.fromkeys(body.keys))  # de-dup while preserving order

    compute_stack = await PlatformConfigService.get(session, "compute_stack") or "kubernetes"

    # Validate against the same canonical list that the post-install
    # Infrastructure > Components page renders; that is the contract the
    # wizard's picker is built on.
    if compute_stack == "kubernetes":
        from app.api.stack_deploy import KUBERNETES_COMPONENTS

        allowed = {c["key"] for c in KUBERNETES_COMPONENTS}
    else:
        # SLURM components are not yet shippable from the wizard.
        allowed = set()

    unknown = [k for k in keys if k not in allowed]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown component keys for compute_stack '{compute_stack}': {', '.join(unknown)}",
        )

    for k in keys:
        await session.execute(
            text(
                "INSERT INTO component_states (component_key, enabled, status, config_json) "
                "VALUES (:k, true, 'queued_for_infra', '{}') "
                "ON CONFLICT (component_key) DO UPDATE SET "
                "enabled = true, status = 'queued_for_infra'"
            ).bindparams(k=k)
        )

    await session.commit()

    return ComponentSelectBatchResponse(queued=keys)


@router.get("", response_model=ComponentListResponse)
async def list_components(session: AsyncSession = Depends(get_session)):
    states = await ComponentService.get_all_states(session)
    state_map = {s.component_key: s for s in states}

    components = []
    for key in COMPONENT_CATALOG:
        components.append(_build_response(key, state_map.get(key)))

    return ComponentListResponse(components=components)
