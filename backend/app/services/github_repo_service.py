"""GitHub repo management service (ADR-043)."""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github_repo import GitHubRepo
from app.services.audit_service import log_action

# Matches git@github.com:owner/repo.git (with optional .git suffix)
GIT_SSH_URL_PATTERN = re.compile(r"^git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")


def _extract_repo_name(git_ssh_url: str) -> str:
    """Extract a display name from a git SSH URL.

    git@github.com:owner/my-repo.git -> my-repo
    """
    parts = git_ssh_url.rstrip("/").split("/")
    name = parts[-1] if parts else git_ssh_url
    if name.endswith(".git"):
        name = name[:-4]
    return name


class GitHubRepoService:
    @staticmethod
    async def list_repos(session: AsyncSession, user_id: int, org_id: int) -> list[GitHubRepo]:
        result = await session.execute(
            select(GitHubRepo)
            .where(GitHubRepo.user_id == user_id, GitHubRepo.organization_id == org_id)
            .order_by(GitHubRepo.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_repo(session: AsyncSession, repo_id: int, user_id: int) -> GitHubRepo | None:
        result = await session.execute(
            select(GitHubRepo).where(GitHubRepo.id == repo_id, GitHubRepo.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_repo(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        git_ssh_url: str,
        display_name: str | None = None,
    ) -> GitHubRepo:
        git_ssh_url = git_ssh_url.strip()

        if not GIT_SSH_URL_PATTERN.match(git_ssh_url):
            raise ValueError("Invalid git SSH URL. Expected format: git@github.com:owner/repo.git")

        resolved_name = display_name.strip() if display_name else _extract_repo_name(git_ssh_url)
        if not resolved_name:
            raise ValueError("Display name cannot be empty")

        # Check for duplicate
        existing = await session.execute(
            select(GitHubRepo).where(
                GitHubRepo.user_id == user_id,
                GitHubRepo.git_ssh_url == git_ssh_url,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("This repository is already configured")

        repo = GitHubRepo(
            user_id=user_id,
            organization_id=org_id,
            git_ssh_url=git_ssh_url,
            display_name=resolved_name,
        )
        session.add(repo)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="github_repo",
            entity_id=repo.id,
            action="create",
            details={"git_ssh_url": git_ssh_url, "display_name": resolved_name},
        )

        return repo

    @staticmethod
    async def delete_repo(session: AsyncSession, repo_id: int, user_id: int) -> None:
        repo = await GitHubRepoService.get_repo(session, repo_id, user_id)
        if not repo:
            raise ValueError("Repository not found")

        await log_action(
            session,
            user_id=user_id,
            entity_type="github_repo",
            entity_id=repo.id,
            action="delete",
            details={"git_ssh_url": repo.git_ssh_url, "display_name": repo.display_name},
        )

        await session.delete(repo)
        await session.flush()
