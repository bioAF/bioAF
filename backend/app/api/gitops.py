from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.gitops import (
    GitCommitDetail,
    GitCommitListResponse,
    GitOpsInitializeRequest,
    GitOpsRepoStatus,
)
from app.services.gitops_service import GitOpsService

router = APIRouter(prefix="/api/gitops", tags=["gitops"])


@router.get("/status", response_model=GitOpsRepoStatus)
async def get_gitops_status(
    current_user: dict = require_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    status = await GitOpsService.get_repo_status(session, org_id)
    return GitOpsRepoStatus(**status)


@router.post("/initialize", response_model=GitOpsRepoStatus)
async def initialize_gitops(
    data: GitOpsInitializeRequest,
    current_user: dict = require_permission("infrastructure", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    # Get org name
    from app.models.organization import Organization
    from sqlalchemy import select

    result = await session.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organization not found")

    try:
        await GitOpsService.initialize_repo(
            session,
            org_id,
            org.name,
            user_id,
            github_pat=data.github_pat,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to initialize GitOps: {e}")

    status = await GitOpsService.get_repo_status(session, org_id)
    return GitOpsRepoStatus(**status)


@router.get("/commits", response_model=GitCommitListResponse)
async def list_commits(
    path: str | None = None,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    repo = await GitOpsService.get_repo(session, org_id)
    if not repo:
        raise HTTPException(400, "GitOps repository not initialized")

    commits = await GitOpsService.list_commits(
        org_id,
        repo.github_repo_name,
        path=path,
        page=page,
        page_size=page_size,
    )
    return GitCommitListResponse(
        commits=commits,
        total=len(commits),
        page=page,
        page_size=page_size,
    )


@router.get("/commits/{sha}", response_model=GitCommitDetail)
async def get_commit(
    sha: str,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    repo = await GitOpsService.get_repo(session, org_id)
    if not repo:
        raise HTTPException(400, "GitOps repository not initialized")

    try:
        detail = await GitOpsService.get_commit(org_id, repo.github_repo_name, sha)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return GitCommitDetail(**detail)


@router.get("/file")
async def get_file(
    path: str,
    ref: str | None = None,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    repo = await GitOpsService.get_repo(session, org_id)
    if not repo:
        raise HTTPException(400, "GitOps repository not initialized")

    try:
        content = await GitOpsService.get_file(org_id, repo.github_repo_name, path, ref=ref)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"path": path, "content": content}
