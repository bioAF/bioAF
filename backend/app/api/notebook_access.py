"""Session lookup and stop authorization shared by both notebook route families.

``/api/notebooks`` and ``/api/v1/notebooks`` keep their own serializers; what
they share is who may reach a session. A session outside the caller's
Organization answers 404 exactly like a missing one, before any adapter call,
record change, or audit entry.
"""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import has_effective_permission
from app.models.notebook_session import NotebookSession
from app.services.notebook_service import NotebookService


async def owned_session_or_404(session: AsyncSession, session_id: int, current_user: dict) -> NotebookSession:
    notebook_session = await NotebookService.get_session(session, session_id, org_id=int(current_user["org_id"]))
    if not notebook_session:
        raise HTTPException(404, "Session not found")
    return notebook_session


async def can_manage_all_sessions(session: AsyncSession, current_user: dict) -> bool:
    """Whether the caller may see and stop other users' sessions.

    ``users:deactivate`` is the established grant for this; an API key must also
    hold it in its scopes.
    """
    return await has_effective_permission(session, current_user, "users", "deactivate")


async def stop_owned_session(session: AsyncSession, session_id: int, current_user: dict) -> NotebookSession:
    """Stop a session in the caller's Organization, if it is theirs or they may manage all."""
    user_id = int(current_user["sub"])
    notebook_session = await owned_session_or_404(session, session_id, current_user)
    if notebook_session.user_id != user_id and not await can_manage_all_sessions(session, current_user):
        raise HTTPException(403, "Can only stop your own sessions")

    await NotebookService.stop_session(session, notebook_session.id, user_id)
    await session.commit()
    return await owned_session_or_404(session, notebook_session.id, current_user)
