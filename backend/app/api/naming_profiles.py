"""HTTP API for Naming Profiles.

Three things changed in the redesign:

1. The response model drops the closed-enum `*_mappings` columns and gains
   `experiment_template_id`.
2. The `POST /test` endpoint now takes an unsaved profile draft inline (with
   one or more filenames) instead of a list of filenames matched against
   every active profile. Profile selection at parse time is a separate
   problem deferred to the auto-ingest rework.
3. The closed enum of field names is gone; segment validation is enforced
   entirely by the Pydantic layer.
"""

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.naming_profile import (
    NamingProfileCreate,
    NamingProfileResponse,
    NamingProfileTestRequest,
    NamingProfileTestResult,
    NamingProfileUpdate,
    SegmentDefinition,
)
from app.services.naming_profile_parser import parse_filename
from app.services.naming_profile_service import NamingProfileService

router = APIRouter(prefix="/api/naming-profiles", tags=["naming_profiles"])


def _profile_response(p) -> NamingProfileResponse:
    return NamingProfileResponse(
        id=p.id,
        organization_id=p.organization_id,
        name=p.name,
        description=p.description,
        delimiter=p.delimiter,
        strip_extension=p.strip_extension,
        segments=[SegmentDefinition(**seg) for seg in (p.segments_json or [])],
        experiment_template_id=p.experiment_template_id,
        status=p.status,
        created_by=p.created_by,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("", response_model=list[NamingProfileResponse])
async def list_profiles(
    status: str | None = None,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    profiles = await NamingProfileService.list_profiles(
        session, org_id, status_filter=status
    )
    return [_profile_response(p) for p in profiles]


@router.post("", response_model=NamingProfileResponse)
async def create_profile(
    body: NamingProfileCreate,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    profile = await NamingProfileService.create_profile(session, org_id, user_id, body)
    await session.commit()
    profile = await NamingProfileService.get_profile(session, profile.id)
    return _profile_response(profile)


@router.get("/{profile_id}", response_model=NamingProfileResponse)
async def get_profile(
    profile_id: int,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    profile = await NamingProfileService.get_profile(session, profile_id)
    if not profile:
        raise HTTPException(404, "Naming profile not found")
    return _profile_response(profile)


@router.put("/{profile_id}", response_model=NamingProfileResponse)
async def update_profile(
    profile_id: int,
    body: NamingProfileUpdate,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    profile = await NamingProfileService.update_profile(
        session, profile_id, user_id, body
    )
    if not profile:
        raise HTTPException(404, "Naming profile not found")
    await session.commit()
    profile = await NamingProfileService.get_profile(session, profile_id)
    return _profile_response(profile)


@router.delete("/{profile_id}", response_model=NamingProfileResponse)
async def deactivate_profile(
    profile_id: int,
    current_user: dict = require_permission("experiments", "delete"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    profile = await NamingProfileService.deactivate_profile(session, profile_id, user_id)
    if not profile:
        raise HTTPException(404, "Naming profile not found")
    await session.commit()
    profile = await NamingProfileService.get_profile(session, profile_id)
    return _profile_response(profile)


@router.post("/test", response_model=list[NamingProfileTestResult])
async def test_profile(
    body: NamingProfileTestRequest,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Parse one or more filenames against an unsaved profile draft.

    Used by the wizard's "Test against a real filename" affordance: the
    profile being authored is sent inline so the user can preview the
    parse before saving.
    """
    draft = SimpleNamespace(
        delimiter=body.delimiter,
        strip_extension=body.strip_extension,
        segments_json=[seg.model_dump() for seg in body.segments],
    )
    results: list[NamingProfileTestResult] = []
    for filename in body.filenames:
        out = parse_filename(filename, draft)
        results.append(
            NamingProfileTestResult(
                filename=filename,
                parsed=out["parsed"],
                unrecognized=out["unrecognized"],
                warnings=out["warnings"],
            )
        )
    return results
