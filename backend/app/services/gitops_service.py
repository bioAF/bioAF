import base64
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import ConflictError, NotFoundError, StateError, ValidationError
from app.models.gitops_repo import GitOpsRepo
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.gitops")

GITHUB_API_BASE = "https://api.github.com"
SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"


class GitOpsService:
    @staticmethod
    async def _get_github_pat() -> str:
        """Fetch GitHub PAT from Secret Manager or settings."""
        if settings.use_secret_manager:
            from app.services.secrets_service import SecretsService

            svc = SecretsService(settings.gcp_project_id)
            secrets = svc.fetch_all()
            pat = secrets.get("bioaf-github-pat")
            if not pat:
                raise ValidationError("GitHub PAT not found in Secret Manager")
            return pat
        # For dev/test, fall back to env var
        import os

        pat = os.environ.get("BIOAF_GITHUB_PAT", "")
        if not pat:
            raise ValidationError("GitHub PAT not configured")
        return pat

    @staticmethod
    def _github_headers(pat: str) -> dict[str, str]:
        return {
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    async def get_repo(session: AsyncSession, org_id: int) -> GitOpsRepo | None:
        result = await session.execute(select(GitOpsRepo).where(GitOpsRepo.organization_id == org_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_repo_status(session: AsyncSession, org_id: int) -> dict:
        repo = await GitOpsService.get_repo(session, org_id)
        if not repo:
            return {
                "initialized": False,
                "repo_url": None,
                "repo_name": None,
                "last_commit_sha": None,
                "last_commit_at": None,
                "status": "not_configured",
            }
        return {
            "initialized": True,
            "repo_url": repo.github_repo_url,
            "repo_name": repo.github_repo_name,
            "last_commit_sha": repo.last_commit_sha,
            "last_commit_at": repo.last_commit_at,
            "status": repo.status,
        }

    @staticmethod
    async def initialize_repo(
        session: AsyncSession,
        org_id: int,
        org_name: str,
        user_id: int,
        github_pat: str | None = None,
    ) -> GitOpsRepo:
        """Create the GitOps repo on GitHub and populate with initial content."""
        existing = await GitOpsService.get_repo(session, org_id)
        if existing:
            raise ConflictError("GitOps repository already initialized")

        pat = github_pat or await GitOpsService._get_github_pat()
        headers = GitOpsService._github_headers(pat)
        repo_name = f"bioaf-infra-{org_name.lower().replace(' ', '-')}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Create repo
            resp = await client.post(
                f"{GITHUB_API_BASE}/user/repos",
                headers=headers,
                json={
                    "name": repo_name,
                    "description": f"bioAF infrastructure GitOps repository for {org_name}",
                    "private": True,
                    "auto_init": True,
                },
            )
            if resp.status_code not in (201, 422):
                raise RuntimeError(f"Failed to create GitHub repo: {resp.status_code} {resp.text}")

            # Get repo info
            user_resp = await client.get(f"{GITHUB_API_BASE}/user", headers=headers)
            owner = user_resp.json().get("login", "bioaf")
            repo_url = f"https://github.com/{owner}/{repo_name}"

            # Create DB record
            repo = GitOpsRepo(
                organization_id=org_id,
                github_repo_url=repo_url,
                github_repo_name=f"{owner}/{repo_name}",
                status="initializing",
                initialized_at=datetime.now(timezone.utc),
            )
            session.add(repo)
            await session.flush()

            # Populate initial content
            initial_files = GitOpsService._collect_initial_files()
            for path, content in initial_files.items():
                await GitOpsService._put_file(
                    client,
                    headers,
                    f"{owner}/{repo_name}",
                    path,
                    content,
                    f"init: add {path}",
                )

            repo.status = "active"
            await session.flush()

            await log_action(
                session,
                user_id=user_id,
                entity_type="gitops",
                entity_id=repo.id,
                action="initialize",
                details={"repo_url": repo_url, "repo_name": repo_name},
            )

        return repo

    @staticmethod
    def _collect_initial_files() -> dict[str, str]:
        """Collect initial files to populate the GitOps repo."""
        files: dict[str, str] = {}

        # Environment files
        scrna_yml = SCRIPTS_DIR / "environments" / "bioaf-scrna.yml"
        if scrna_yml.exists():
            files["environments/bioaf-scrna.yml"] = scrna_yml.read_text()

        r_script = SCRIPTS_DIR / "environments" / "r-bioaf.R"
        if r_script.exists():
            files["environments/bioaf-rstudio.yml"] = r_script.read_text()

        # Pipeline defaults
        defaults_dir = SCRIPTS_DIR / "pipelines" / "defaults"
        if defaults_dir.exists():
            for f in defaults_dir.iterdir():
                if f.suffix == ".json":
                    dir_name = f.stem
                    files[f"pipelines/{dir_name}/bioaf-defaults.json"] = f.read_text()

        # Placeholder directories via .gitkeep
        for d in ["terraform", "containers", "config", "notebooks", "environments/custom", "pipelines/custom"]:
            files[f"{d}/.gitkeep"] = ""

        # Template notebooks
        templates_dir = SCRIPTS_DIR / "notebooks" / "templates"
        if templates_dir.exists():
            for nb_file in sorted(templates_dir.glob("*.ipynb")):
                files[f"notebooks/{nb_file.name}"] = nb_file.read_text()

        return files

    @staticmethod
    async def _put_file(
        client: httpx.AsyncClient,
        headers: dict,
        repo_full_name: str,
        path: str,
        content: str,
        message: str,
    ) -> str | None:
        """Create or update a file in the GitHub repo. Returns commit SHA."""
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{path}"

        # Check if file exists (to get SHA for update)
        existing_sha = None
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            existing_sha = resp.json().get("sha")

        payload: dict = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
        }
        if existing_sha:
            payload["sha"] = existing_sha

        resp = await client.put(url, headers=headers, json=payload)
        if resp.status_code in (200, 201):
            return resp.json().get("commit", {}).get("sha")
        logger.warning("Failed to put file %s: %d %s", path, resp.status_code, resp.text)
        return None

    @staticmethod
    async def commit_and_push(
        session: AsyncSession,
        org_id: int,
        user_id: int | None,
        files: dict[str, str],
        message: str,
    ) -> str:
        """Commit one or more file changes to the GitOps repo. Returns commit SHA."""
        repo = await GitOpsService.get_repo(session, org_id)
        if not repo:
            raise StateError("GitOps repository not initialized")

        pat = await GitOpsService._get_github_pat()
        headers = GitOpsService._github_headers(pat)
        last_sha = None

        async with httpx.AsyncClient(timeout=30.0) as client:
            for path, content in files.items():
                sha = await GitOpsService._put_file(
                    client,
                    headers,
                    repo.github_repo_name,
                    path,
                    content,
                    message,
                )
                if sha:
                    last_sha = sha

        if last_sha:
            repo.last_commit_sha = last_sha
            repo.last_commit_at = datetime.now(timezone.utc)
            await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="gitops",
            entity_id=repo.id,
            action="commit",
            details={"message": message, "files": list(files.keys()), "sha": last_sha},
        )

        return last_sha or ""

    @staticmethod
    async def get_file(org_id: int, repo_full_name: str, path: str, ref: str | None = None) -> str:
        """Read a file from the GitOps repo."""
        pat = await GitOpsService._get_github_pat()
        headers = GitOpsService._github_headers(pat)
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{path}"
        params = {}
        if ref:
            params["ref"] = ref

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                raise NotFoundError(f"File not found: {path} (status {resp.status_code})")
            data = resp.json()
            return base64.b64decode(data["content"]).decode()

    @staticmethod
    async def list_commits(
        org_id: int,
        repo_full_name: str,
        path: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict]:
        """List commits, optionally filtered by path."""
        pat = await GitOpsService._get_github_pat()
        headers = GitOpsService._github_headers(pat)
        params: dict = {"page": page, "per_page": page_size}
        if path:
            params["path"] = path

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/commits",
                headers=headers,
                params=params,
            )
            if resp.status_code != 200:
                return []
            commits = resp.json()
            return [
                {
                    "sha": c["sha"],
                    "message": c["commit"]["message"],
                    "author": c["commit"]["author"]["name"],
                    "timestamp": c["commit"]["author"]["date"],
                    "files_changed": None,
                }
                for c in commits
            ]

    @staticmethod
    async def get_commit(org_id: int, repo_full_name: str, sha: str) -> dict:
        """Get commit detail with diff."""
        pat = await GitOpsService._get_github_pat()
        headers = GitOpsService._github_headers(pat)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/commits/{sha}",
                headers=headers,
            )
            if resp.status_code != 200:
                raise NotFoundError(f"Commit not found: {sha}")
            data = resp.json()
            files = [f["filename"] for f in data.get("files", [])]
            diff = "\n".join(f.get("patch", "") for f in data.get("files", []))
            return {
                "sha": data["sha"],
                "message": data["commit"]["message"],
                "author": data["commit"]["author"]["name"],
                "timestamp": data["commit"]["author"]["date"],
                "diff": diff,
                "files": files,
            }

    @staticmethod
    async def diff_commits(org_id: int, repo_full_name: str, sha1: str, sha2: str) -> str:
        """Get diff between two commits."""
        pat = await GitOpsService._get_github_pat()
        headers = GitOpsService._github_headers(pat)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{repo_full_name}/compare/{sha1}...{sha2}",
                headers=headers,
            )
            if resp.status_code != 200:
                raise NotFoundError(f"Could not compare commits {sha1}...{sha2}")
            data = resp.json()
            return "\n".join(f.get("patch", "") for f in data.get("files", []))

    @staticmethod
    async def revert_to_commit(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        repo_full_name: str,
        path: str,
        target_sha: str,
    ) -> str:
        """Revert a file to a previous commit's content. Returns new commit SHA."""
        old_content = await GitOpsService.get_file(org_id, repo_full_name, path, ref=target_sha)
        sha = await GitOpsService.commit_and_push(
            session,
            org_id,
            user_id,
            files={path: old_content},
            message=f"revert: {path} to commit {target_sha[:8]}",
        )
        return sha
