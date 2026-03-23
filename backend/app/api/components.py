from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.component import ComponentConfigUpdate, ComponentListResponse, ComponentStateResponse
from app.services.component_service import COMPONENT_CATALOG, ComponentService
from app.services import role_service
from app.services.terraform_service import TerraformService

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


@router.get("", response_model=ComponentListResponse)
async def list_components(session: AsyncSession = Depends(get_session)):
    states = await ComponentService.get_all_states(session)
    state_map = {s.component_key: s for s in states}

    components = []
    for key in COMPONENT_CATALOG:
        components.append(_build_response(key, state_map.get(key)))

    return ComponentListResponse(components=components)


@router.get("/{key}", response_model=ComponentStateResponse)
async def get_component(key: str, session: AsyncSession = Depends(get_session)):
    if key not in COMPONENT_CATALOG:
        raise HTTPException(status_code=404, detail=f"Component '{key}' not found")

    state = await ComponentService.get_state(session, key)
    return _build_response(key, state)


@router.post("/{key}/enable")
async def enable_component(key: str, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = await _require_admin(request, session)
    user_id = int(current_user["sub"])

    if key not in COMPONENT_CATALOG:
        raise HTTPException(status_code=404, detail=f"Component '{key}' not found")

    # Check dependencies
    states = await ComponentService.get_all_states(session)
    enabled_keys = {s.component_key for s in states if s.enabled}
    missing_deps = ComponentService.check_dependencies(key, enabled_keys)
    if missing_deps:
        dep_names = [COMPONENT_CATALOG[d]["name"] for d in missing_deps if d in COMPONENT_CATALOG]
        raise HTTPException(
            status_code=400,
            detail=f"Missing dependencies: {', '.join(dep_names)}. Enable them first.",
        )

    # Enable the component
    state = await ComponentService.enable_component(session, key, user_id)

    # Generate terraform plan
    tf_var_key = f"enable_{key}"
    run = await TerraformService.generate_plan(session, user_id, key, {tf_var_key: True})

    await session.commit()

    return {
        "message": f"Component '{key}' enable plan generated",
        "component": _build_response(key, state),
        "terraform_run_id": run.id,
        "plan_summary": run.plan_summary_json,
        "status": run.status,
    }


@router.post("/{key}/disable")
async def disable_component(key: str, request: Request, session: AsyncSession = Depends(get_session)):
    current_user = await _require_admin(request, session)
    user_id = int(current_user["sub"])

    if key not in COMPONENT_CATALOG:
        raise HTTPException(status_code=404, detail=f"Component '{key}' not found")

    # Check for dependents that are currently enabled
    dependents = ComponentService.get_dependents(key)
    states = await ComponentService.get_all_states(session)
    enabled_keys = {s.component_key for s in states if s.enabled}
    active_dependents = [d for d in dependents if d in enabled_keys]

    cascade_warning = None
    if active_dependents:
        dep_names = [COMPONENT_CATALOG[d]["name"] for d in active_dependents if d in COMPONENT_CATALOG]
        cascade_warning = f"Disabling will also disable: {', '.join(dep_names)}"

    # Disable the component
    state = await ComponentService.disable_component(session, key, user_id)

    # Generate terraform plan
    tf_var_key = f"enable_{key}"
    changes = {tf_var_key: False}
    # Also disable dependents
    for dep_key in active_dependents:
        changes[f"enable_{dep_key}"] = False
        await ComponentService.disable_component(session, dep_key, user_id)

    run = await TerraformService.generate_plan(session, user_id, key, changes)

    await session.commit()

    response = {
        "message": f"Component '{key}' disable plan generated",
        "component": _build_response(key, state),
        "terraform_run_id": run.id,
        "plan_summary": run.plan_summary_json,
        "status": run.status,
    }
    if cascade_warning:
        response["cascade_warning"] = cascade_warning
    return response


@router.patch("/{key}/configure")
async def configure_component(
    key: str, body: ComponentConfigUpdate, request: Request, session: AsyncSession = Depends(get_session)
):
    current_user = await _require_admin(request, session)
    user_id = int(current_user["sub"])

    if key not in COMPONENT_CATALOG:
        raise HTTPException(status_code=404, detail=f"Component '{key}' not found")

    state = await ComponentService.update_config(session, key, body.config, user_id)

    # Generate plan with new config
    run = await TerraformService.generate_plan(session, user_id, key, body.config)

    await session.commit()

    return {
        "message": f"Component '{key}' configuration updated, plan generated",
        "component": _build_response(key, state),
        "terraform_run_id": run.id,
        "plan_summary": run.plan_summary_json,
    }
