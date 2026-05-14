"""Dependencies for /api/v1/integrations/* routes.

- `require_api_key_permission(resource, action)`: enforces both the SA role's
  permission and the key's scope envelope. JWT callers are rejected here
  because the public surface is for API keys only.
- `idempotent_create(...)`: helper that handles Idempotency-Key replay.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services import role_service


def require_api_key_permission(resource: str, action: str):
    """Like dependencies.require_permission() but additionally rejects any
    caller that is not API-key authenticated. The integration sub-app is
    API-key only by contract."""

    async def checker(request: Request, session: AsyncSession = Depends(get_session)):
        user = getattr(request.state, "current_user", None)
        if user is None:
            raise HTTPException(401, "Missing credentials")
        if user.get("api_key_id") is None:
            raise HTTPException(401, "API key required")
        if "role_id" not in user:
            raise HTTPException(401, "Token missing role_id")
        if not await role_service.has_permission(session, int(user["role_id"]), resource, action):
            raise HTTPException(403, "role_missing")
        needed = f"{resource}:{action}"
        if needed not in (user.get("scopes") or []):
            raise HTTPException(403, "key_scope_missing")
        return user

    return Depends(checker)


def get_api_key_user(request: Request) -> dict:
    """Return the current API-key-authenticated principal or 401."""
    user = getattr(request.state, "current_user", None)
    if user is None or user.get("api_key_id") is None:
        raise HTTPException(401, "API key required")
    return user
