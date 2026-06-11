"""Association endpoints: link a Paper to a global/project/experiment scope."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.literature import AssociationCreateRequest, AssociationPayload
from app.services.literature import association_service, paper_service
from app.services.literature.paper_service import PaperNotFound

router = APIRouter()


@router.get("/papers/{paper_id}/associations", response_model=list[AssociationPayload])
async def list_associations_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    items = []
    for a in await association_service.list_for_paper(session, paper_id):
        scope_name = await paper_service.scope_name_for(session, a.scope_type, a.scope_id)
        parent_pid, parent_pname = await paper_service.parent_project_for(session, a.scope_type, a.scope_id)
        items.append(
            AssociationPayload(
                id=a.id,
                scope_type=a.scope_type,
                scope_id=a.scope_id,
                scope_name=scope_name,
                parent_project_id=parent_pid,
                parent_project_name=parent_pname,
                added_by_user_id=a.added_by_user_id,
                added_at=a.added_at,
            )
        )
    return items


@router.post("/papers/{paper_id}/associations", response_model=AssociationPayload)
async def create_association_endpoint(
    paper_id: int,
    body: AssociationCreateRequest,
    current_user: dict = require_permission("literature", "associate"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    try:
        assoc = await association_service.get_or_create(
            session,
            paper=paper,
            user_id=int(current_user["sub"]),
            scope_type=body.scope_type,
            scope_id=body.scope_id,
        )
    except association_service.InvalidScope as e:
        raise HTTPException(400, str(e))
    await session.commit()
    scope_name = await paper_service.scope_name_for(session, assoc.scope_type, assoc.scope_id)
    parent_pid, parent_pname = await paper_service.parent_project_for(session, assoc.scope_type, assoc.scope_id)
    return AssociationPayload(
        id=assoc.id,
        scope_type=assoc.scope_type,
        scope_id=assoc.scope_id,
        scope_name=scope_name,
        parent_project_id=parent_pid,
        parent_project_name=parent_pname,
        added_by_user_id=assoc.added_by_user_id,
        added_at=assoc.added_at,
    )


@router.delete("/papers/{paper_id}/associations/{association_id}", status_code=204)
async def delete_association_endpoint(
    paper_id: int,
    association_id: int,
    current_user: dict = require_permission("literature", "associate"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    try:
        await association_service.soft_remove(session, association_id=association_id, user_id=int(current_user["sub"]))
    except association_service.AssociationNotFound:
        raise HTTPException(404, "association not found")
    await session.commit()
