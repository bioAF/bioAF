from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.experiment import UserSummary
from app.schemas.template import TemplateCreate, TemplateResponse, TemplateUpdate
from app.services.template_service import TemplateService

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _template_response(t) -> TemplateResponse:
    return TemplateResponse(
        id=t.id,
        name=t.name,
        description=t.description,
        required_fields_json=t.required_fields_json,
        custom_fields_schema_json=t.custom_fields_schema_json,
        created_by=UserSummary(id=t.created_by.id, name=t.created_by.name, email=t.created_by.email)
        if t.created_by
        else None,
        created_at=t.created_at,
    )


@router.get("")
async def list_templates(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])
    templates = await TemplateService.list_templates(session, org_id)
    return [_template_response(t) for t in templates]


@router.post("", response_model=TemplateResponse)
async def create_template(
    body: TemplateCreate,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    template = await TemplateService.create_template(session, org_id, user_id, body)
    await session.commit()

    template = await TemplateService.get_template(session, template.id, org_id)
    return _template_response(template)


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    template = await TemplateService.get_template(session, template_id, org_id)
    if not template:
        raise HTTPException(404, "Template not found")
    return _template_response(template)


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: int,
    body: TemplateUpdate,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    template = await TemplateService.update_template(session, template_id, user_id, body)
    if not template:
        raise HTTPException(404, "Template not found")

    await session.commit()
    template = await TemplateService.get_template(session, template_id, org_id)
    return _template_response(template)
