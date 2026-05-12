import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
from app.models.nf_core_registry_refresh import NfCoreRegistryRefresh

logger = logging.getLogger("bioaf.nf_core_registry")

# Pipelines for which bioAF ships a tailored QC dashboard template.
# Unknown pipelines fall back to "generic".
QC_TEMPLATE_MAP: dict[str, str] = {
    "scrnaseq": "scrnaseq",
    "rnaseq": "rnaseq",
}


class NfCoreRegistryService:
    REGISTRY_URL = "https://nf-co.re/pipelines.json"
    HTTP_TIMEOUT_SECONDS = 30.0

    @staticmethod
    async def _get_or_create_refresh_row(session: AsyncSession) -> NfCoreRegistryRefresh:
        row = (
            await session.execute(select(NfCoreRegistryRefresh).where(NfCoreRegistryRefresh.id == 1))
        ).scalar_one_or_none()
        if row is None:
            row = NfCoreRegistryRefresh(id=1)
            session.add(row)
            await session.flush()
        return row

    @staticmethod
    def _normalize_releases(releases: list[dict], default_branch: str | None) -> list[dict]:
        """Drop the dev pseudo-release and keep only fields the frontend renders."""
        normalized: list[dict] = []
        for rel in releases:
            tag = rel.get("tag_name")
            if not tag or tag == "dev" or (default_branch and tag == default_branch and tag in {"main", "master"}):
                # Skip the dev pseudo-release; nf-core uses tag_name == "dev" for it.
                if tag == "dev":
                    continue
            normalized.append(
                {
                    "tag_name": tag,
                    "published_at": rel.get("published_at"),
                    "has_schema": bool(rel.get("has_schema")),
                }
            )
        # Newest first: nf-core already orders this way but enforce defensively by published_at desc.
        normalized.sort(key=lambda r: r.get("published_at") or "", reverse=True)
        return normalized

    @staticmethod
    async def refresh_registry(session: AsyncSession) -> dict:
        """Fetch nf-co.re/pipelines.json, upsert rows, archive missing ones.

        Returns {fetched, archived, error}. On fetch failure, existing rows are
        preserved and the error is recorded on the singleton refresh row.
        """
        refresh_row = await NfCoreRegistryService._get_or_create_refresh_row(session)
        refresh_row.last_attempt_at = datetime.now(timezone.utc)

        try:
            async with httpx.AsyncClient(timeout=NfCoreRegistryService.HTTP_TIMEOUT_SECONDS) as client:
                response = await client.get(NfCoreRegistryService.REGISTRY_URL)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            logger.error("nf-core registry fetch failed: %s", err)
            refresh_row.last_error = err
            return {"fetched": 0, "archived": 0, "error": err}

        workflows = payload.get("remote_workflows") or []
        now = datetime.now(timezone.utc)
        seen_names: set[str] = set()

        existing_rows = (await session.execute(select(NfCoreRegistryPipeline))).scalars().all()
        existing_by_name = {row.name: row for row in existing_rows}

        fetched = 0
        for wf in workflows:
            name = wf.get("name")
            if not name:
                continue
            seen_names.add(name)
            full_name = wf.get("full_name") or f"nf-core/{name}"
            releases = NfCoreRegistryService._normalize_releases(
                wf.get("releases") or [], wf.get("default_branch")
            )
            latest = releases[0]["tag_name"] if releases else None

            row = existing_by_name.get(name)
            if row is None:
                row = NfCoreRegistryPipeline(name=name)
                session.add(row)

            row.full_name = full_name
            row.description = wf.get("description")
            row.topics = wf.get("topics") or []
            row.stars = wf.get("stargazers_count")
            row.default_branch = wf.get("default_branch")
            row.releases_json = releases
            row.latest_release = latest
            row.archived = bool(wf.get("archived", False))
            row.last_seen_at = now
            row.fetched_at = now
            fetched += 1

        # Anything we didn't see this refresh -> archive
        archived = 0
        for row in existing_rows:
            if row.name not in seen_names and not row.archived:
                row.archived = True
                archived += 1

        await session.flush()
        refresh_row.last_success_at = now
        refresh_row.last_error = None
        return {"fetched": fetched, "archived": archived, "error": None}
