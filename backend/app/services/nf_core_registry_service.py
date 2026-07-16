import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import String, cast, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline
from app.models.nf_core_registry_refresh import NfCoreRegistryRefresh
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.services.audit_service import log_action
from app.services.pipeline_catalog_service import PipelineCatalogService

logger = logging.getLogger("bioaf.nf_core_registry")

# Pipelines for which bioAF ships a tailored QC dashboard template.
# Unknown pipelines fall back to "generic".
QC_TEMPLATE_MAP: dict[str, str] = {
    "scrnaseq": "scrnaseq",
    "rnaseq": "bulk_rnaseq",
    "chipseq": "chipseq",
    "atacseq": "atacseq",
}


class NfCoreRegistryService:
    REGISTRY_URL = "https://nf-co.re/pipelines.json"
    HTTP_TIMEOUT_SECONDS = 30.0

    class PipelineAlreadyInstalledError(Exception):
        """Raised when install_pipeline is asked to add a pipeline_key that
        already exists in the org's catalog. Callers should map this to 409."""

    class PipelineNotInRegistryError(Exception):
        """Raised when install_pipeline is asked for a pipeline name that
        the registry cache has never heard of. Callers should map this to 404."""

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
            releases = NfCoreRegistryService._normalize_releases(wf.get("releases") or [], wf.get("default_branch"))
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

    @staticmethod
    async def list_pipelines_with_status(
        session: AsyncSession,
        org_id: int,
        q: str | None = None,
        only_installed: bool = False,
        include_archived: bool = False,
    ) -> list[dict]:
        """Return registry rows joined with the org's pipeline_catalog so the
        UI can render install state. Each row is a dict shaped for the API
        response."""
        join_key = literal("nf-core/").concat(NfCoreRegistryPipeline.name)
        stmt = (
            select(NfCoreRegistryPipeline, PipelineCatalogEntry.version)
            .outerjoin(
                PipelineCatalogEntry,
                (PipelineCatalogEntry.pipeline_key == join_key) & (PipelineCatalogEntry.organization_id == org_id),
            )
            .order_by(NfCoreRegistryPipeline.name)
        )
        if not include_archived:
            stmt = stmt.where(NfCoreRegistryPipeline.archived == False)  # noqa: E712
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                or_(
                    NfCoreRegistryPipeline.name.ilike(like),
                    NfCoreRegistryPipeline.description.ilike(like),
                    # topic match: cast jsonb to text and ilike against the array literal
                    cast(NfCoreRegistryPipeline.topics, String).ilike(like),
                )
            )
        result = await session.execute(stmt)
        rows = result.all()

        out: list[dict] = []
        for entry, installed_version in rows:
            installed = installed_version is not None
            update_available = bool(installed and entry.latest_release and installed_version != entry.latest_release)
            if only_installed and not installed:
                continue
            out.append(
                {
                    "name": entry.name,
                    "full_name": entry.full_name,
                    "description": entry.description,
                    "topics": entry.topics or [],
                    "stars": entry.stars,
                    "latest_release": entry.latest_release,
                    "archived": entry.archived,
                    "installed": installed,
                    "installed_version": installed_version,
                    "update_available": update_available,
                }
            )
        return out

    @staticmethod
    async def get_pipeline_versions(session: AsyncSession, name: str) -> list[dict]:
        """Return the list of release tags for a pipeline (newest first, dev filtered)."""
        row = (
            await session.execute(select(NfCoreRegistryPipeline).where(NfCoreRegistryPipeline.name == name))
        ).scalar_one_or_none()
        if row is None or not row.releases_json:
            return []
        return list(row.releases_json)

    @staticmethod
    async def get_last_refreshed_at(session: AsyncSession) -> datetime | None:
        row = (
            await session.execute(select(NfCoreRegistryRefresh).where(NfCoreRegistryRefresh.id == 1))
        ).scalar_one_or_none()
        return row.last_success_at if row else None

    @staticmethod
    async def install_pipeline(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        name: str,
        version: str,
        *,
        via_assistant: bool = False,
    ) -> PipelineCatalogEntry:
        """Install an nf-core pipeline as a catalog entry for the given org."""
        registry_row = (
            await session.execute(select(NfCoreRegistryPipeline).where(NfCoreRegistryPipeline.name == name))
        ).scalar_one_or_none()
        if registry_row is None:
            raise NfCoreRegistryService.PipelineNotInRegistryError(name)

        pipeline_key = f"nf-core/{name}"
        existing = (
            await session.execute(
                select(PipelineCatalogEntry).where(
                    PipelineCatalogEntry.organization_id == org_id,
                    PipelineCatalogEntry.pipeline_key == pipeline_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise NfCoreRegistryService.PipelineAlreadyInstalledError(pipeline_key)

        source_url = f"https://github.com/{registry_row.full_name}"
        schema = await PipelineCatalogService.fetch_pipeline_schema(source_url, version)
        qc_template = QC_TEMPLATE_MAP.get(name, "generic")

        entry = PipelineCatalogEntry(
            organization_id=org_id,
            pipeline_key=pipeline_key,
            name=registry_row.full_name,
            description=registry_row.description,
            source_type="nf-core",
            source_url=source_url,
            version=version,
            schema_json=schema or None,
            is_builtin=False,
            enabled=True,
            qc_template=qc_template,
        )
        session.add(entry)
        await session.flush()

        install_details: dict[str, object] = {
            "name": name,
            "version": version,
            "source_url": source_url,
            "qc_template": qc_template,
        }
        if via_assistant:
            install_details["via_assistant"] = True
        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_catalog",
            entity_id=entry.id,
            action="install_from_nf_core_registry",
            details=install_details,
        )
        return entry
