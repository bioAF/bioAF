"""Upgrade service with GitHub-based version checking and upgrade management."""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.upgrade_history import UpgradeHistory
from app.services.event_bus import event_bus
from app.services.event_types import PLATFORM_UPDATE_AVAILABLE

logger = logging.getLogger("bioaf.upgrade_service")

GITHUB_REPO = "not-that-guy-again/bioAF"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Cache for version check results
_version_cache: dict = {}
_version_cache_time: datetime | None = None
CACHE_TTL_SECONDS = 3600  # 1 hour


def _clear_version_cache() -> None:
    """Clear the version check cache (used by tests)."""
    global _version_cache, _version_cache_time
    _version_cache = {}
    _version_cache_time = None


def _parse_version(tag: str) -> tuple[int, ...]:
    """Parse a version tag like 'v1.2.3' or '1.2.3' into a comparable tuple."""
    cleaned = re.sub(r"^v", "", tag.strip())
    parts = cleaned.split(".")
    return tuple(int(p) for p in parts if p.isdigit())


class UpgradeService:
    @staticmethod
    async def get_version_info() -> dict:
        """Returns current version, build date, commit hash."""
        return {
            "current_version": settings.app_version,
            "app_name": settings.app_name,
            "build_date": None,
            "commit_hash": None,
        }

    @staticmethod
    async def check_for_updates(org_id: int) -> dict:
        """Query GitHub Releases API for bioaf repo, compare against current version."""
        global _version_cache, _version_cache_time

        now = datetime.now(timezone.utc)
        if _version_cache_time and (now - _version_cache_time).total_seconds() < CACHE_TTL_SECONDS:
            return _version_cache

        current = settings.app_version
        result = {
            "current_version": current,
            "latest_version": current,
            "update_available": False,
            "changelog": None,
            "release_url": None,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    GITHUB_API_URL,
                    headers={"Accept": "application/vnd.github+json"},
                )

            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "")
                latest = re.sub(r"^v", "", tag.strip())
                if latest and _parse_version(latest) > _parse_version(current):
                    result["update_available"] = True
                result["latest_version"] = latest or current
                result["changelog"] = data.get("body")
                result["release_url"] = data.get("html_url")
            else:
                logger.warning("GitHub API returned %s checking for updates", resp.status_code)
        except Exception:
            logger.warning("Failed to check GitHub for updates", exc_info=True)

        _version_cache = result
        _version_cache_time = now
        return result

    @staticmethod
    async def get_upgrade_history(
        session: AsyncSession,
        org_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[UpgradeHistory], int]:
        count_result = await session.execute(
            select(func.count(UpgradeHistory.id)).where(
                UpgradeHistory.organization_id == org_id,
            )
        )
        total = count_result.scalar() or 0

        result = await session.execute(
            select(UpgradeHistory)
            .where(UpgradeHistory.organization_id == org_id)
            .order_by(UpgradeHistory.started_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        upgrades = list(result.scalars().all())
        return upgrades, total

    @staticmethod
    async def start_upgrade(
        session: AsyncSession,
        org_id: int,
        target_version: str,
        user_id: int,
    ) -> UpgradeHistory:
        """Initiate upgrade: create upgrade_history record with plan."""
        current = settings.app_version

        upgrade = UpgradeHistory(
            organization_id=org_id,
            from_version=current,
            to_version=target_version,
            status="started",
            started_by_user_id=user_id,
            terraform_plan_json={"note": "Terraform plan would be generated here in production"},
        )
        session.add(upgrade)
        await session.flush()
        return upgrade

    @staticmethod
    async def confirm_upgrade(
        session: AsyncSession,
        org_id: int,
        upgrade_id: int,
    ) -> UpgradeHistory:
        """Confirm and execute upgrade."""
        result = await session.execute(
            select(UpgradeHistory).where(
                UpgradeHistory.id == upgrade_id,
                UpgradeHistory.organization_id == org_id,
            )
        )
        upgrade = result.scalar_one_or_none()
        if not upgrade:
            raise ValueError("Upgrade not found")

        if upgrade.status != "started":
            raise ValueError(f"Upgrade is in '{upgrade.status}' state, cannot confirm")

        # In production: rolling update GKE pods, run migrations, apply terraform
        upgrade.status = "completed"
        upgrade.completed_at = datetime.now(timezone.utc)
        await session.flush()
        return upgrade

    @staticmethod
    async def rollback(
        session: AsyncSession,
        org_id: int,
        upgrade_id: int,
        user_id: int,
    ) -> UpgradeHistory:
        """Rollback an upgrade."""
        result = await session.execute(
            select(UpgradeHistory).where(
                UpgradeHistory.id == upgrade_id,
                UpgradeHistory.organization_id == org_id,
            )
        )
        upgrade = result.scalar_one_or_none()
        if not upgrade:
            raise ValueError("Upgrade not found")

        if upgrade.status != "completed":
            raise ValueError(f"Upgrade is in '{upgrade.status}' state, cannot rollback")

        # In production: revert container image, optionally revert terraform
        upgrade.status = "rolled_back"
        upgrade.notes = f"Rolled back by user {user_id}"
        upgrade.completed_at = datetime.now(timezone.utc)
        await session.flush()
        return upgrade

    @staticmethod
    async def background_version_check(org_id: int) -> None:
        """Daily version check, emits event if new version found."""
        result = await UpgradeService.check_for_updates(org_id)
        if result.get("update_available"):
            asyncio.create_task(
                event_bus.emit(
                    PLATFORM_UPDATE_AVAILABLE,
                    {
                        "event_type": PLATFORM_UPDATE_AVAILABLE,
                        "org_id": org_id,
                        "title": f"Platform update available: {result['latest_version']}",
                        "message": f"A new version ({result['latest_version']}) is available. Current: {result['current_version']}",
                        "severity": "info",
                        "summary": f"Update available: {result['latest_version']}",
                    },
                )
            )
