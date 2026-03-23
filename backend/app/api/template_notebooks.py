from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.template_notebook import (
    TemplateCloneRequest,
    TemplateNotebookListResponse,
    TemplateNotebookResponse,
)
from app.services.template_notebook_service import TemplateNotebookService

router = APIRouter(prefix="/api/template-notebooks", tags=["template-notebooks"])


@router.get("", response_model=TemplateNotebookListResponse)
async def list_templates(
    current_user: dict = require_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    # Initialize built-in templates on first access
    await TemplateNotebookService.initialize_builtin_templates(session, org_id)
    await session.commit()

    templates = await TemplateNotebookService.list_templates(session, org_id)
    return TemplateNotebookListResponse(
        notebooks=[
            TemplateNotebookResponse(
                id=t.id,
                name=t.name,
                description=t.description,
                category=t.category,
                compatible_with=t.compatible_with,
                parameters=t.parameters_json,
                is_builtin=t.is_builtin,
                created_at=t.created_at,
            )
            for t in templates
        ],
        total=len(templates),
    )


@router.get("/{template_id}", response_model=TemplateNotebookResponse)
async def get_template(
    template_id: int,
    current_user: dict = require_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    template = await TemplateNotebookService.get_template(session, org_id, template_id)
    if not template:
        raise HTTPException(404, "Template notebook not found")

    return TemplateNotebookResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        category=template.category,
        compatible_with=template.compatible_with,
        parameters=template.parameters_json,
        is_builtin=template.is_builtin,
        created_at=template.created_at,
    )


@router.get("/{template_id}/content")
async def get_template_content(
    template_id: int,
    current_user: dict = require_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    template = await TemplateNotebookService.get_template(session, org_id, template_id)
    if not template:
        raise HTTPException(404, "Template notebook not found")

    try:
        content = await TemplateNotebookService.get_template_content(org_id, template)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"notebook_path": template.notebook_path, "content": content}


@router.post("/{template_id}/clone")
async def clone_template(
    template_id: int,
    data: TemplateCloneRequest,
    current_user: dict = require_permission("notebooks", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        output_path = await TemplateNotebookService.clone_template(
            session,
            org_id,
            user_id,
            template_id,
            new_name=data.new_name,
            experiment_id=data.experiment_id,
            parameter_overrides=data.parameters,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"output_path": output_path}
