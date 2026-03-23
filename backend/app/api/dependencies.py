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
            raise HTTPException(403, "Insufficient permissions")
        return user

    return Depends(checker)
