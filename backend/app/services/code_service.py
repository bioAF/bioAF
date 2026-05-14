"""Auto-generation of project codes and experiment codes."""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org_code_counter import OrgCodeCounter
from app.models.organization import Organization

PROJECT_CODE_KIND = "project"
EXPERIMENT_CODE_KIND = "experiment"

# Extension → data type label mapping
_EXT_DATA_TYPES: dict[str, str] = {
    ".fastq.gz": "FQ",
    ".fastq": "FQ",
    ".fq.gz": "FQ",
    ".fq": "FQ",
    ".bam": "BAM",
    ".bai": "BAI",
    ".h5ad": "counts",
    ".loom": "counts",
    ".csv": "data",
    ".tsv": "data",
    ".txt": "data",
    ".pdf": "report",
    ".html": "report",
    ".png": "plot",
    ".jpg": "plot",
    ".jpeg": "plot",
    ".svg": "plot",
}


class CodeService:
    # ------------------------------------------------------------------
    # Code format helpers (pure, no DB)
    # ------------------------------------------------------------------

    @staticmethod
    def derive_org_prefix(org_name: str) -> str:
        """Lowercase, drop non-alphanumeric, take first 4 chars.

        Examples:
        - "bioAF" -> "bioa"
        - "Acme & Co" -> "acme"
        - "42 Bio" -> "42bi"
        - "X" -> "x"
        Returns "" if no alphanumerics in the name (caller must handle).
        """
        cleaned = re.sub(r"[^a-z0-9]", "", org_name.lower())
        return cleaned[:4]

    @staticmethod
    def format_project_code(org_prefix: str, counter: int) -> str:
        """Build a project code like 'bioap-0008'."""
        return f"{org_prefix}p-{counter:04d}"

    @staticmethod
    def format_experiment_code(org_prefix: str, counter: int) -> str:
        """Build an experiment code like 'bioae-0025'."""
        return f"{org_prefix}e-{counter:04d}"

    # ------------------------------------------------------------------
    # Filename suggestion (pure, no DB)
    # ------------------------------------------------------------------

    @staticmethod
    def _split_extension(filename: str) -> tuple[str, str]:
        """Return (stem, ext) where ext handles double extensions like .fastq.gz."""
        lower = filename.lower()
        for double_ext in (".fastq.gz", ".fq.gz", ".tar.gz", ".tar.bz2"):
            if lower.endswith(double_ext):
                return filename[: -len(double_ext)], filename[-len(double_ext) :]
        if "." in filename:
            stem, ext = filename.rsplit(".", 1)
            return stem, f".{ext}"
        return filename, ""

    @staticmethod
    def _infer_data_type(filename: str) -> str | None:
        """Infer a data_type label from the file extension."""
        lower = filename.lower()
        for ext, label in _EXT_DATA_TYPES.items():
            if lower.endswith(ext):
                return label
        return None

    @staticmethod
    def suggest_filename(
        original: str,
        project_code: str | None,
        experiment_code: str | None,
        sample_id: str | None,
        data_type: str | None,
        date_str: str,
    ) -> str:
        """Return a suggested filename following the naming convention.

        Pattern: {ProjectCode}_{ExperimentCode}_{SampleID}_{DataType}_{YYYYMMDD}.ext
        Segments are omitted when the corresponding value is None/empty.
        Returns *original* unchanged when no association is provided.
        """
        if not project_code and not experiment_code and not sample_id:
            return original

        _, ext = CodeService._split_extension(original)
        # Infer data_type from extension if not provided
        effective_type = data_type or CodeService._infer_data_type(original)

        segments: list[str] = []
        if project_code:
            segments.append(project_code)
        if experiment_code:
            segments.append(experiment_code)
        if sample_id:
            segments.append(sample_id)
        if effective_type:
            segments.append(effective_type)
        segments.append(date_str)

        stem = "_".join(segments)
        return f"{stem}{ext}"

    # ------------------------------------------------------------------
    # DB-integrated helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _next_counter(session: AsyncSession, org_id: int, kind: str) -> int:
        """Atomically allocate the next counter value for (org_id, kind).

        Treats the counter as an odometer: monotonically increasing, never
        reset, unaffected by deletes.
        """
        row = (
            await session.execute(
                select(OrgCodeCounter)
                .where(OrgCodeCounter.organization_id == org_id, OrgCodeCounter.kind == kind)
                .with_for_update()
            )
        ).scalar_one_or_none()

        if row is None:
            row = OrgCodeCounter(organization_id=org_id, kind=kind, next_value=1)
            session.add(row)
            await session.flush()

        value = row.next_value
        row.next_value = value + 1
        await session.flush()
        return value

    @staticmethod
    async def _org_prefix_for(session: AsyncSession, org_id: int) -> str:
        org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        prefix = CodeService.derive_org_prefix(org.name or "")
        return prefix or "org"

    @staticmethod
    async def next_project_code(session: AsyncSession, org_id: int, name: str) -> str:
        """Return the next project code (e.g., 'bioap-0008') for the org.

        The ``name`` argument is retained for backwards-compatible call sites
        but is no longer used: codes are derived from the organization name.
        """
        del name  # unused; org prefix drives code now
        prefix = await CodeService._org_prefix_for(session, org_id)
        counter = await CodeService._next_counter(session, org_id, PROJECT_CODE_KIND)
        return CodeService.format_project_code(prefix, counter)

    @staticmethod
    async def next_experiment_code(session: AsyncSession, org_id: int, project_id: int | None) -> str:
        """Return the next experiment code (e.g., 'bioae-0025') for the org.

        The ``project_id`` argument is retained for backwards-compatible call
        sites but is no longer used: the counter is per-org, not per-project.
        """
        del project_id  # unused; counter is org-scoped now
        prefix = await CodeService._org_prefix_for(session, org_id)
        counter = await CodeService._next_counter(session, org_id, EXPERIMENT_CODE_KIND)
        return CodeService.format_experiment_code(prefix, counter)
