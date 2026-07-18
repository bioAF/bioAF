from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services import role_service


def require_beta_feature(key: str):
    """Gate a router/route behind a beta flag. When the flag is off the feature must look like it does
    not exist, so this raises 404 (not 403). Used to hide the lit_validation endpoints when its flag is
    off, matching the UI which hides the nav entry (spec-07)."""

    async def checker(session: AsyncSession = Depends(get_session)):
        from app.services import beta_features_service

        if not await beta_features_service.is_enabled(session, key):
            raise HTTPException(404, "Not Found")
        return True

    return Depends(checker)


def require_permission(resource: str, action: str):
    async def checker(request: Request, session: AsyncSession = Depends(get_session)):
        user = request.state.current_user
        if "role_id" not in user:
            raise HTTPException(401, "Token missing role_id; please log in again")
        role_id = int(user["role_id"])
        if not await role_service.has_permission(session, role_id, resource, action):
            raise HTTPException(403, "role_missing")
        # API-key-authenticated requests narrow the role permissions to the
        # key's scope envelope (ADR-049). JWT requests have api_key_id=None
        # and skip the scope check.
        if user.get("api_key_id") is not None:
            scopes = user.get("scopes") or []
            needed = f"{resource}:{action}"
            if needed not in scopes:
                raise HTTPException(403, "key_scope_missing")
        return user

    return Depends(checker)


def require_any_permission(permissions: list[tuple[str, str]]):
    """Pass if the user's role holds ANY of the listed (resource, action)
    permissions. Used where a surface is reachable from more than one place,
    e.g. the QC report is viewable by anyone who can view experiments or
    pipelines."""

    async def checker(request: Request, session: AsyncSession = Depends(get_session)):
        user = request.state.current_user
        if "role_id" not in user:
            raise HTTPException(401, "Token missing role_id; please log in again")
        role_id = int(user["role_id"])
        granted = [
            (resource, action)
            for resource, action in permissions
            if await role_service.has_permission(session, role_id, resource, action)
        ]
        if not granted:
            raise HTTPException(403, "role_missing")
        # For API-key requests, at least one granted permission must also fall
        # inside the key's scope envelope (ADR-049). JWT requests skip this.
        if user.get("api_key_id") is not None:
            scopes = user.get("scopes") or []
            if not any(f"{resource}:{action}" in scopes for resource, action in granted):
                raise HTTPException(403, "key_scope_missing")
        return user

    return Depends(checker)


# "View Results" = able to view experiments OR pipelines. The QC report (and the
# AI reviews on it) is reached through both the Experiment Results tab and the
# Pipeline Run Results tab, so either view permission grants read access.
RESULTS_VIEW_PERMISSIONS: list[tuple[str, str]] = [
    ("experiments", "view"),
    ("pipelines", "view"),
]


def require_results_view():
    return require_any_permission(RESULTS_VIEW_PERMISSIONS)
