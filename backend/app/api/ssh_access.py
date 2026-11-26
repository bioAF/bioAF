"""Settings > System Settings > Security and SSH access (plan 03B).

Work Node ranges need work_nodes:configure; application-host settings, cloud recovery and the
Restore SSH access link need infrastructure:configure. Each caller sees only the sections their
effective permissions cover; custom roles with the same grants work the same way.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import has_effective_permission, require_any_permission, require_permission
from app.database import get_session
from app.schemas.ssh_access import ApplyRequest, WorkNodeDraftRequest
from app.services import ssh_access_policy as policy

router = APIRouter(prefix="/api/v1/settings/ssh-access", tags=["ssh-access"])

_SECTION_PERMISSIONS = [("work_nodes", "configure"), ("infrastructure", "configure")]


def _actor(current_user: dict) -> int:
    return int(current_user["sub"])


@router.get("")
async def get_ssh_access(
    request: Request,
    current_user: dict = require_any_permission(_SECTION_PERMISSIONS),
    session: AsyncSession = Depends(get_session),
):
    body: dict = {}
    if await has_effective_permission(session, current_user, "work_nodes", "configure"):
        body["work_nodes"] = await policy.work_node_policy_view(session)
    if await has_effective_permission(session, current_user, "infrastructure", "configure"):
        from app.services.ssh_recovery import application_host_view

        body["application_host"] = await application_host_view(session, int(current_user["org_id"]))
    return body


@router.put("/work-nodes/draft")
async def save_work_node_draft(
    body: WorkNodeDraftRequest,
    current_user: dict = require_permission("work_nodes", "configure"),
    session: AsyncSession = Depends(get_session),
):
    draft = await policy.save_work_node_draft(
        session,
        enabled=body.enabled,
        ranges=[r.model_dump() for r in body.ranges],
        base_version=body.base_version,
        actor_id=_actor(current_user),
    )
    await session.commit()
    return {"draft": draft}


@router.post("/work-nodes/check")
async def check_work_node_draft(
    current_user: dict = require_permission("work_nodes", "configure"),
    session: AsyncSession = Depends(get_session),
):
    return await policy.check_work_node_draft(session)


@router.post("/work-nodes/apply")
async def apply_work_node_draft(
    body: ApplyRequest,
    current_user: dict = require_permission("work_nodes", "configure"),
    session: AsyncSession = Depends(get_session),
):
    applied = await policy.apply_work_node_draft(
        session, draft_version=body.draft_version, actor_id=_actor(current_user)
    )
    await session.commit()
    return {"applied": applied}


@router.post("/work-nodes/rollback")
async def rollback_work_node_policy(
    current_user: dict = require_permission("work_nodes", "configure"),
    session: AsyncSession = Depends(get_session),
):
    try:
        applied = await policy.rollback_work_node_policy(session, actor_id=_actor(current_user))
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    return {"applied": applied}


@router.get("/restore")
async def get_restore_link(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Restore SSH access: a read-only link to this server's console page (plan 03B, D03-9).

    Changes no key, rule or session and uses no bioAF cloud credential.
    """
    from app.services.ssh_recovery import restore_view

    return await restore_view(session)


@router.put("/host/draft")
async def save_host_filter_draft(
    body: WorkNodeDraftRequest,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    from app.services import host_ssh_access

    draft = await host_ssh_access.save_draft(
        session,
        enabled=body.enabled,
        ranges=[r.model_dump() for r in body.ranges],
        base_version=body.base_version,
        actor_id=_actor(current_user),
    )
    await session.commit()
    return {"draft": draft}


@router.post("/host/check")
async def check_host_access(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Read-only automatic preflight; also asks the host for fresh fixed guest diagnostics."""
    from app.services import host_ssh_access

    return await host_ssh_access.check_access(session)


@router.post("/host/apply")
async def apply_host_filter(
    body: ApplyRequest,
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
):
    """Reconcile protected recovery, re-check, then apply the filter (a cloud change: infrastructure:deploy)."""
    from app.services import host_ssh_access

    return {
        "applied": await host_ssh_access.apply_draft(
            session, draft_version=body.draft_version, actor_id=_actor(current_user)
        )
    }


@router.post("/host/recovery")
async def set_up_host_recovery(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
):
    """Create or repair protected cloud recovery access (a cloud change: infrastructure:deploy).

    For installs from before recovery access existed; no SSH filter or installer rerun is needed.
    """
    from app.services import host_ssh_access

    return {"recovery": await host_ssh_access.set_up_recovery(session, actor_id=_actor(current_user))}


@router.post("/host/rollback")
async def rollback_host_filter(
    current_user: dict = require_permission("infrastructure", "deploy"),
    session: AsyncSession = Depends(get_session),
):
    from app.services import host_ssh_access

    return {"applied": await host_ssh_access.rollback(session, actor_id=_actor(current_user))}


@router.get("/observed-address")
async def get_observed_address(
    request: Request,
    current_user: dict = require_any_permission(_SECTION_PERMISSIONS),
):
    """The address the managed edge saw for this browser, as a draft suggestion only (plan 03B).

    Trusted only with the installation's edge token, which nginx adds after overwriting X-Real-IP
    with its own observed peer. Custom upstream proxies are not inferred.
    """
    import hmac
    import ipaddress

    from app.config import settings

    unavailable = {
        "address": None,
        "message": "bioAF cannot see this computer's address through this connection. Enter it yourself.",
    }
    token = request.headers.get("X-Bioaf-Edge-Token", "")
    if not settings.edge_token or not hmac.compare_digest(token.encode(), settings.edge_token.encode()):
        return unavailable
    try:
        address = ipaddress.ip_address(request.headers.get("X-Real-IP", "").strip())
    except ValueError:
        return unavailable
    return {"address": f"{address}/{address.max_prefixlen}", "message": None}
