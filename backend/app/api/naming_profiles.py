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
from app.services.naming_profile_service import NamingProfileService

router = APIRouter(prefix="/api/naming-profiles", tags=["naming_profiles"])


def _profile_response(p, match_count: int | None = None) -> NamingProfileResponse:
    return NamingProfileResponse(
        id=p.id,
        organization_id=p.organization_id,
        name=p.name,
        description=p.description,
        delimiter=p.delimiter,
        strip_extension=p.strip_extension,
        segments=[SegmentDefinition(**seg) for seg in p.segments_json] if p.segments_json else [],
        project_code_mappings=p.project_code_mappings or {},
        experiment_code_mappings=p.experiment_code_mappings or {},
        status=p.status,
        created_by=p.created_by,
        created_at=p.created_at,
        updated_at=p.updated_at,
        match_count_30d=match_count,
    )


@router.get("", response_model=list[NamingProfileResponse])
async def list_profiles(
    status: str | None = None,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    profiles = await NamingProfileService.list_profiles(session, org_id, status_filter=status)
    results = []
    for p in profiles:
        count = await NamingProfileService.get_match_statistics(session, p.id)
        results.append(_profile_response(p, match_count=count))
    return results


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
    count = await NamingProfileService.get_match_statistics(session, profile_id)
    return _profile_response(profile, match_count=count)


@router.put("/{profile_id}", response_model=NamingProfileResponse)
async def update_profile(
    profile_id: int,
    body: NamingProfileUpdate,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    profile = await NamingProfileService.update_profile(session, profile_id, user_id, body)
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
async def test_profiles(
    body: NamingProfileTestRequest,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    results = await NamingProfileService.test_profiles(session, org_id, body.filenames)
    return [NamingProfileTestResult(**r) for r in results]
