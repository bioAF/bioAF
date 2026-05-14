from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services import role_service


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
