import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.pipeline_catalog")

BUILTIN_PIPELINES = [
    {
        "pipeline_key": "nf-core/scrnaseq",
        "name": "nf-core/scrnaseq",
        "description": "Single-cell RNA-seq analysis pipeline using nf-core/scrnaseq. Supports STARsolo, Cell Ranger, Alevin, and Kallisto.",
        "source_type": "nf-core",
        "source_url": "https://github.com/nf-core/scrnaseq",
        "version": "2.7.1",
        "defaults_file": "nf-core-scrnaseq.json",
    },
    {
        "pipeline_key": "nf-core/rnaseq",
        "name": "nf-core/rnaseq",
        "description": "Bulk RNA-seq analysis pipeline using nf-core/rnaseq. Alignment, quantification, and QC.",
        "source_type": "nf-core",
        "source_url": "https://github.com/nf-core/rnaseq",
        "version": "3.14.0",
        "defaults_file": "nf-core-rnaseq.json",
    },
    {
        "pipeline_key": "nf-core/fetchngs",
        "name": "nf-core/fetchngs",
        "description": "Download sequencing data from public databases (SRA, ENA, DDBJ, GEO, Synapse).",
        "source_type": "nf-core",
        "source_url": "https://github.com/nf-core/fetchngs",
        "version": "1.12.0",
        "defaults_file": "nf-core-fetchngs.json",
    },
    {
        "pipeline_key": "bioaf-system-test",
        "name": "bioAF System Test",
        "description": "Verifies end-to-end pipeline execution: reads input files from GCS, processes them, writes output files to GCS.",
        "source_type": "builtin",
        "source_url": "",
        "version": "1.0.0",
        "defaults_file": "bioaf-system-test.json",
    },
]

DEFAULTS_DIR = Path(__file__).parent.parent / "pipeline_defaults"


class PipelineCatalogService:
    @staticmethod
    async def initialize_builtin_pipelines(session: AsyncSession, org_id: int) -> list[PipelineCatalogEntry]:
        """Seed the catalog with built-in nf-core pipelines if they don't exist."""
        created = []
        for pipeline_def in BUILTIN_PIPELINES:
            result = await session.execute(
                select(PipelineCatalogEntry).where(
                    PipelineCatalogEntry.organization_id == org_id,
                    PipelineCatalogEntry.pipeline_key == pipeline_def["pipeline_key"],
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                # Sync defaults from disk when they differ from what is in the DB
                defaults_file = DEFAULTS_DIR / pipeline_def["defaults_file"]
                if defaults_file.exists():
                    disk_defaults = json.loads(defaults_file.read_text())
                    if existing.default_params_json != disk_defaults:
                        existing.default_params_json = disk_defaults
                        logger.info("Refreshed defaults for %s", pipeline_def["pipeline_key"])
                continue

            default_params = {}
            defaults_file = DEFAULTS_DIR / pipeline_def["defaults_file"]
            if defaults_file.exists():
                default_params = json.loads(defaults_file.read_text())

            entry = PipelineCatalogEntry(
                organization_id=org_id,
                pipeline_key=pipeline_def["pipeline_key"],
                name=pipeline_def["name"],
                description=pipeline_def["description"],
                source_type=pipeline_def["source_type"],
                source_url=pipeline_def["source_url"],
                version=pipeline_def["version"],
                default_params_json=default_params,
                is_builtin=True,
                enabled=True,
            )
            session.add(entry)
            created.append(entry)

        if created:
            await session.flush()
            logger.info("Initialized %d built-in pipelines for org %d", len(created), org_id)

        return created

    @staticmethod
    async def fetch_pipeline_schema(source_url: str, version: str | None) -> dict:
        """Fetch nextflow_schema.json from a Git repo. Mock-friendly via httpx."""
        import httpx

        # Convert GitHub URL to raw content URL
        # e.g. https://github.com/nf-core/scrnaseq -> raw.githubusercontent.com/nf-core/scrnaseq/{version}/nextflow_schema.json
        raw_url = source_url.replace("github.com", "raw.githubusercontent.com")
        branch = version or "master"
        schema_url = f"{raw_url}/{branch}/nextflow_schema.json"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(schema_url)
            if response.status_code == 200:
                return response.json()
            logger.warning("Failed to fetch schema from %s: %d", schema_url, response.status_code)
            return {}

    @staticmethod
    async def list_pipelines(session: AsyncSession, org_id: int) -> list[PipelineCatalogEntry]:
        result = await session.execute(
            select(PipelineCatalogEntry)
            .where(
                PipelineCatalogEntry.organization_id == org_id,
                PipelineCatalogEntry.enabled == True,  # noqa: E712
            )
            .order_by(PipelineCatalogEntry.name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_pipeline(session: AsyncSession, org_id: int, pipeline_key: str) -> PipelineCatalogEntry | None:
        result = await session.execute(
            select(PipelineCatalogEntry).where(
                PipelineCatalogEntry.organization_id == org_id,
                PipelineCatalogEntry.pipeline_key == pipeline_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def add_custom_pipeline(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        name: str,
        source_url: str,
        version: str | None = None,
        description: str | None = None,
    ) -> PipelineCatalogEntry:
        """Register a custom pipeline by Git URL."""
        # Generate pipeline_key from name
        pipeline_key = name.lower().replace(" ", "-")

        # Try to fetch schema
        schema = await PipelineCatalogService.fetch_pipeline_schema(source_url, version)

        entry = PipelineCatalogEntry(
            organization_id=org_id,
            pipeline_key=pipeline_key,
            name=name,
            description=description,
            source_type="git",
            source_url=source_url,
            version=version,
            schema_json=schema if schema else None,
            is_builtin=False,
            enabled=True,
        )
        session.add(entry)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_catalog",
            entity_id=entry.id,
            action="add_custom",
            details={"name": name, "source_url": source_url, "version": version},
        )
        return entry

    @staticmethod
    async def update_pipeline_version(
        session: AsyncSession,
        pipeline_id: int,
        user_id: int,
        version: str,
    ) -> PipelineCatalogEntry | None:
        result = await session.execute(select(PipelineCatalogEntry).where(PipelineCatalogEntry.id == pipeline_id))
        entry = result.scalar_one_or_none()
        if not entry:
            return None

        old_version = entry.version
        entry.version = version

        # Re-fetch schema for new version
        if entry.source_url:
            schema = await PipelineCatalogService.fetch_pipeline_schema(entry.source_url, version)
            if schema:
                entry.schema_json = schema

        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="pipeline_catalog",
            entity_id=entry.id,
            action="update_version",
            details={"version": version},
            previous_value={"version": old_version},
        )
        return entry
