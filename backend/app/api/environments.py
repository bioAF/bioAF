from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.environment import (
    EnvironmentChangeResponse,
    EnvironmentCreateRequest,
    EnvironmentDetailResponse,
    EnvironmentDiff,
    EnvironmentHistoryResponse,
    EnvironmentListResponse,
    EnvironmentResponse,
    EnvironmentRollbackRequest,
)
from app.schemas.package import InstalledPackageResponse
from app.services.environment_service import EnvironmentService
from app.services.environment_history_service import EnvironmentHistoryService

router = APIRouter(prefix="/api/environments", tags=["environments"])


@router.get("", response_model=EnvironmentListResponse)
async def list_environments(
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    # Initialize defaults on first access
    await EnvironmentService.initialize_default_environments(session, org_id)
    await session.commit()

    envs = await EnvironmentService.list_environments(session, org_id)
    return EnvironmentListResponse(
        environments=[EnvironmentResponse(**e) for e in envs],
        total=len(envs),
    )


@router.get("/{name}", response_model=EnvironmentDetailResponse)
async def get_environment(
    name: str,
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    env = await EnvironmentService.get_environment(session, org_id, name)
    if not env:
        raise HTTPException(404, "Environment not found")

    packages = [
        InstalledPackageResponse(
            name=p.package_name,
            version=p.version,
            source=p.source,
            pinned=p.pinned,
            installed_at=p.installed_at,
        )
        for p in (env.packages or [])
    ]

    return EnvironmentDetailResponse(
        id=env.id,
        name=env.name,
        env_type=env.env_type,
        description=env.description,
        is_default=env.is_default,
        jupyter_kernel_name=env.jupyter_kernel_name,
        status=env.status,
        packages=packages,
        last_synced_at=env.last_synced_at,
        created_by={
            "id": env.created_by.id,
            "name": env.created_by.name,
            "email": env.created_by.email,
        }
        if env.created_by
        else None,
        created_at=env.created_at,
    )


@router.post("", response_model=EnvironmentResponse)
async def create_environment(
    data: EnvironmentCreateRequest,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        env = await EnvironmentService.create_custom_environment(
            session,
            org_id,
            user_id,
            name=data.name,
            description=data.description,
            clone_from=data.clone_from,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentResponse(
        id=env.id,
        name=env.name,
        env_type=env.env_type,
        description=env.description,
        is_default=env.is_default,
        package_count=0,
        jupyter_kernel_name=env.jupyter_kernel_name,
        status=env.status,
        last_synced_at=env.last_synced_at,
        created_by=None,
        created_at=env.created_at,
    )


@router.post("/{name}/clone", response_model=EnvironmentResponse)
async def clone_environment(
    name: str,
    data: EnvironmentCreateRequest,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        env = await EnvironmentService.create_custom_environment(
            session,
            org_id,
            user_id,
            name=data.name,
            description=data.description,
            clone_from=name,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentResponse(
        id=env.id,
        name=env.name,
        env_type=env.env_type,
        description=env.description,
        is_default=env.is_default,
        package_count=0,
        jupyter_kernel_name=env.jupyter_kernel_name,
        status=env.status,
        last_synced_at=env.last_synced_at,
        created_by=None,
        created_at=env.created_at,
    )


@router.delete("/{name}", status_code=204)
async def archive_environment(
    name: str,
    current_user: dict = require_permission("environments", "delete"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        await EnvironmentService.archive_environment(session, org_id, user_id, name)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{name}/packages")
async def list_environment_packages(
    name: str,
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    env = await EnvironmentService.get_environment(session, org_id, name)
    if not env:
        raise HTTPException(404, "Environment not found")

    packages = [
        InstalledPackageResponse(
            name=p.package_name,
            version=p.version,
            source=p.source,
            pinned=p.pinned,
            installed_at=p.installed_at,
        )
        for p in (env.packages or [])
    ]
    return {"packages": packages, "total": len(packages)}


@router.get("/{name}/history", response_model=EnvironmentHistoryResponse)
async def get_environment_history(
    name: str,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    env = await EnvironmentService.get_environment(session, org_id, name)
    if not env:
        raise HTTPException(404, "Environment not found")

    changes, total = await EnvironmentHistoryService.get_change_timeline(
        session,
        org_id,
        env.id,
        page=page,
        page_size=page_size,
    )

    return EnvironmentHistoryResponse(
        changes=[
            EnvironmentChangeResponse(
                id=c.id,
                change_type=c.change_type,
                package_name=c.package_name,
                old_version=c.old_version,
                new_version=c.new_version,
                git_commit_sha=c.git_commit_sha,
                commit_message=c.commit_message,
                reconciled=c.reconciled,
                reconciled_at=c.reconciled_at,
                error_message=c.error_message,
                user={"id": c.user.id, "name": c.user.name, "email": c.user.email} if c.user else None,
                created_at=c.created_at,
            )
            for c in changes
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{name}/history/{change_id}", response_model=EnvironmentChangeResponse)
async def get_change_detail(
    name: str,
    change_id: int,
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    change = await EnvironmentHistoryService.get_change_detail(session, org_id, change_id)
    if not change:
        raise HTTPException(404, "Change not found")

    return EnvironmentChangeResponse(
        id=change.id,
        change_type=change.change_type,
        package_name=change.package_name,
        old_version=change.old_version,
        new_version=change.new_version,
        git_commit_sha=change.git_commit_sha,
        commit_message=change.commit_message,
        reconciled=change.reconciled,
        reconciled_at=change.reconciled_at,
        error_message=change.error_message,
        user={"id": change.user.id, "name": change.user.name, "email": change.user.email} if change.user else None,
        created_at=change.created_at,
    )


@router.post("/{name}/rollback", response_model=EnvironmentChangeResponse)
async def rollback_environment(
    name: str,
    data: EnvironmentRollbackRequest,
    current_user: dict = require_permission("environments", "build"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    env = await EnvironmentService.get_environment(session, org_id, name)
    if not env:
        raise HTTPException(404, "Environment not found")

    try:
        change = await EnvironmentHistoryService.rollback_environment(
            session,
            org_id,
            user_id,
            env.id,
            data.target_change_id,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentChangeResponse(
        id=change.id,
        change_type=change.change_type,
        package_name=change.package_name,
        old_version=change.old_version,
        new_version=change.new_version,
        git_commit_sha=change.git_commit_sha,
        commit_message=change.commit_message,
        reconciled=change.reconciled,
        reconciled_at=change.reconciled_at,
        error_message=change.error_message,
        user=None,
        created_at=change.created_at,
    )


@router.get("/{name}/compare", response_model=EnvironmentDiff)
async def compare_environments(
    name: str,
    sha1: str,
    sha2: str,
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    try:
        diff = await EnvironmentHistoryService.compare_environments(
            session,
            org_id,
            name,
            sha1,
            sha2,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return EnvironmentDiff(**diff)
