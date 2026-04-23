"""GitHub repo API endpoints (ADR-043).

CRUD for user-scoped GitHub repos used by work nodes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.github_repo import (
    GitHubRepoCreateRequest,
    GitHubRepoListResponse,
    GitHubRepoResponse,
)
from app.services.github_repo_service import GitHubRepoService

router = APIRouter(prefix="/api/v1/github-repos", tags=["github-repos"])


@router.get("", response_model=GitHubRepoListResponse)
async def list_github_repos(
    current_user: dict = require_permission("work_nodes", "view"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])
    repos = await GitHubRepoService.list_repos(session, user_id, org_id)
    return GitHubRepoListResponse(
        repos=[GitHubRepoResponse.model_validate(r) for r in repos],
        total=len(repos),
    )


@router.post("", response_model=GitHubRepoResponse)
async def create_github_repo(
    body: GitHubRepoCreateRequest,
    current_user: dict = require_permission("work_nodes", "launch"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])
    try:
        repo = await GitHubRepoService.create_repo(session, user_id, org_id, body.git_ssh_url, body.display_name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    return GitHubRepoResponse.model_validate(repo)


@router.delete("/{repo_id}")
async def delete_github_repo(
    repo_id: int,
    current_user: dict = require_permission("work_nodes", "launch"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    try:
        await GitHubRepoService.delete_repo(session, repo_id, user_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    await session.commit()
    return {"status": "ok"}
