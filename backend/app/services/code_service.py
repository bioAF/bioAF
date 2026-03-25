"""Auto-generation of project codes and experiment codes."""

import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

VOWELS = set("aeiouAEIOU")

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
    # Project code helpers (pure, no DB)
    # ------------------------------------------------------------------

    @staticmethod
    def derive_project_prefix(name: str) -> str:
        """Return the consonant-initial prefix from a project name.

        For each space-delimited token, take the first consonant (a,e,i,o,u
        are vowels). Tokens with no consonants are skipped. Result is
        uppercased. Falls back to 'PRJ' if no consonants found.
        """
        tokens = name.split()
        consonants: list[str] = []
        for token in tokens:
            for ch in token:
                if ch.isalpha() and ch not in VOWELS:
                    consonants.append(ch.upper())
                    break
        return "".join(consonants) if consonants else "PRJ"

    @staticmethod
    def generate_project_code(name: str, year: int, existing_codes: list[str]) -> str:
        """Build a project code like CS26-1.

        Counter is per-prefix-per-year: looks at *existing_codes* for entries
        matching ``{prefix}{2-digit-year}-N`` and picks the next integer.
        """
        prefix = CodeService.derive_project_prefix(name)
        yr = str(year)[-2:]  # 2-digit year
        pattern = re.compile(rf"^{re.escape(prefix)}{re.escape(yr)}-(\d+)$")

        max_counter = 0
        for code in existing_codes:
            m = pattern.match(code)
            if m:
                max_counter = max(max_counter, int(m.group(1)))

        return f"{prefix}{yr}-{max_counter + 1}"

    @staticmethod
    def generate_experiment_code(existing_count: int) -> str:
        """Return E001, E002, ... based on how many experiments already exist."""
        return f"E{existing_count + 1:03d}"

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
    async def next_project_code(session: AsyncSession, org_id: int, name: str) -> str:
        """Query existing project codes for the org and return the next code."""
        from app.models.project import Project

        year = datetime.now().year
        rows = await session.execute(
            select(Project.code).where(
                Project.organization_id == org_id,
                Project.code.isnot(None),
            )
        )
        existing = [r[0] for r in rows.all() if r[0]]
        return CodeService.generate_project_code(name, year, existing)

    @staticmethod
    async def next_experiment_code(session: AsyncSession, org_id: int, project_id: int | None) -> str:
        """Return the next experiment code (E001, E002, ...) for the project."""
        from app.models.experiment import Experiment

        where = [Experiment.organization_id == org_id]
        if project_id is not None:
            where.append(Experiment.project_id == project_id)

        result = await session.execute(select(func.count()).select_from(Experiment).where(*where))
        count = result.scalar_one()
        return CodeService.generate_experiment_code(count)
