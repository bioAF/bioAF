from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime

from pydantic import BaseModel

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.package import (
    DependencyTree,
    PackageInstallRequest,
    PackageRemoveRequest,
    PackageSearchResponse,
    PackageUpdateRequest,
)
from app.services.package_search_service import PackageSearchService
from app.services.package_service import PackageService


class EnvironmentChangeResponse(BaseModel):
    """Legacy schema for package change tracking (pre-ADR-033)."""

    id: int
    change_type: str
    package_name: str | None = None
    old_version: str | None = None
    new_version: str | None = None
    git_commit_sha: str | None = None
    commit_message: str | None = None
    reconciled: bool
    reconciled_at: datetime | None = None
    error_message: str | None = None
    user: dict | None = None
    created_at: datetime


router = APIRouter(prefix="/api/packages", tags=["packages"])


def _change_response(change) -> dict:
    return {
        "id": change.id,
        "change_type": change.change_type,
        "package_name": change.package_name,
        "old_version": change.old_version,
        "new_version": change.new_version,
        "git_commit_sha": change.git_commit_sha,
        "commit_message": change.commit_message,
        "reconciled": change.reconciled,
        "reconciled_at": change.reconciled_at,
        "error_message": change.error_message,
        "user": None,
        "created_at": change.created_at,
    }


@router.get("/search", response_model=PackageSearchResponse)
async def search_packages(
    query: str,
    sources: str | None = None,
    limit: int = 20,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    source_list = sources.split(",") if sources else None
    results = await PackageSearchService.search_packages(query, source_list, limit)
    return PackageSearchResponse(results=results, total=len(results), query=query)


@router.get("/dependencies", response_model=DependencyTree)
async def get_dependencies(
    package_name: str,
    source: str,
    environment: str,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    result = await PackageSearchService.get_dependency_tree(package_name, source, environment)
    return DependencyTree(**result)


@router.post("/install", response_model=EnvironmentChangeResponse)
async def install_package(
    data: PackageInstallRequest,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        change = await PackageService.install_package(
            session,
            org_id,
            user_id,
            environment_name=data.environment,
            package_name=data.package_name,
            version=data.version,
            source=data.source,
            pinned=data.pinned,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentChangeResponse(**_change_response(change))


@router.post("/remove", response_model=EnvironmentChangeResponse)
async def remove_package(
    data: PackageRemoveRequest,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        change = await PackageService.remove_package(
            session,
            org_id,
            user_id,
            environment_name=data.environment,
            package_name=data.package_name,
            source=data.source,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentChangeResponse(**_change_response(change))


@router.post("/update", response_model=EnvironmentChangeResponse)
async def update_package(
    data: PackageUpdateRequest,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        change = await PackageService.update_package(
            session,
            org_id,
            user_id,
            environment_name=data.environment,
            package_name=data.package_name,
            new_version=data.new_version,
            source=data.source,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentChangeResponse(**_change_response(change))


@router.post("/{name}/pin", response_model=EnvironmentChangeResponse)
async def pin_package(
    name: str,
    environment: str,
    version: str,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        change = await PackageService.pin_package(
            session,
            org_id,
            user_id,
            environment,
            name,
            version,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentChangeResponse(**_change_response(change))


@router.post("/{name}/unpin", response_model=EnvironmentChangeResponse)
async def unpin_package(
    name: str,
    environment: str,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        change = await PackageService.unpin_package(
            session,
            org_id,
            user_id,
            environment,
            name,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentChangeResponse(**_change_response(change))
