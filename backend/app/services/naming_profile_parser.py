"""Naming profile parser engine for CRO filename parsing.

Parses filenames against configurable naming profiles to extract
project codes, experiment codes, sample IDs, dates, and other metadata.
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.naming_profile import NamingProfile
from app.models.project import Project
from app.models.sample import Sample


@dataclass
class ParseResult:
    success: bool
    profile_id: int | None = None
    profile_name: str | None = None
    segments: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass
class MatchResult:
    status: str  # "matched", "multiple_matches", "unmatched"
    parse_result: ParseResult | None = None
    candidate_profile_ids: list[int] = field(default_factory=list)
    candidate_results: list[ParseResult] = field(default_factory=list)


@dataclass
class EntityResolution:
    project_id: int | None = None
    project_name: str | None = None
    experiment_id: int | None = None
    experiment_name: str | None = None
    sample_id: int | None = None
    sample_name: str | None = None


DATE_PATTERNS = {
    "YYYY-MM-DD": r"^\d{4}-\d{2}-\d{2}$",
    "YYYYMMDD": r"^\d{8}$",
}

VERSION_PATTERN = r"^[vV]\d+$"
SAMPLE_INDEX_PATTERN = re.compile(r"^[Ss]?(\d+)$")


def _strip_extension(filename: str) -> str:
    """Remove file extension, handling double extensions like .fastq.gz."""
    p = PurePosixPath(filename)
    # Handle double extensions
    if p.suffix in (".gz", ".bz2", ".xz") and PurePosixPath(p.stem).suffix:
        return PurePosixPath(p.stem).stem
    return p.stem


def _validate_date(value: str, fmt: str | None) -> bool:
    """Validate a date segment value against a format pattern."""
    if not fmt:
        # Accept any recognized date format
        return any(re.match(pat, value) for pat in DATE_PATTERNS.values())
    pattern = DATE_PATTERNS.get(fmt)
    if pattern:
        return bool(re.match(pattern, value))
    return True  # Unknown format, accept


def _validate_version(value: str, fmt: str | None) -> bool:
    """Validate a version segment (e.g. v001, v01, v1)."""
    return bool(re.match(VERSION_PATTERN, value))


def parse_filename(filename: str, profile: NamingProfile) -> ParseResult:
    """Parse a single filename against a single naming profile.

    Returns a ParseResult with success=True if the filename matches,
    or success=False with an error message if it doesn't.
    """
    segments_config = profile.segments_json
    if not isinstance(segments_config, list):
        return ParseResult(success=False, error="Invalid segments configuration")

    # Step 1: Optionally strip extension
    name = _strip_extension(filename) if profile.strip_extension else filename

    # Step 2: Split by delimiter
    parts = name.split(profile.delimiter)

    # Step 3: Check segment count
    if len(parts) != len(segments_config):
        return ParseResult(
            success=False,
            error=f"Expected {len(segments_config)} segments, got {len(parts)}",
        )

    # Step 4: Validate and parse each segment
    parsed = {}
    for seg_def in segments_config:
        pos = seg_def.get("position", 0)
        seg_field = seg_def.get("field", "ignore")
        seg_format = seg_def.get("format")
        required = seg_def.get("required", True)

        if pos >= len(parts):
            if required:
                return ParseResult(success=False, error=f"Segment at position {pos} out of range")
            continue

        value = parts[pos]

        # Check required fields are not empty
        if required and not value.strip():
            return ParseResult(success=False, error=f"Required segment '{seg_field}' at position {pos} is empty")

        # Skip empty optional segments
        if not value.strip() and not required:
            continue

        # Field-specific validation
        if seg_field == "date":
            if not _validate_date(value, seg_format):
                if required:
                    return ParseResult(success=False, error=f"Invalid date format at position {pos}: '{value}'")
                continue
        elif seg_field == "version":
            if not _validate_version(value, seg_format):
                if required:
                    return ParseResult(success=False, error=f"Invalid version format at position {pos}: '{value}'")
                continue
        elif seg_field == "sample_index":
            m = SAMPLE_INDEX_PATTERN.match(value)
            if not m:
                if required:
                    return ParseResult(
                        success=False,
                        error=f"Invalid sample_index format at position {pos}: '{value}'",
                    )
                continue
            # Store the numeric part only (strip S prefix)
            parsed["sample_index"] = m.group(1)
            continue

        if seg_field != "ignore":
            parsed[seg_field] = value

    return ParseResult(
        success=True,
        profile_id=profile.id,
        profile_name=profile.name,
        segments=parsed,
    )


def match_filename(filename: str, profiles: list[NamingProfile]) -> MatchResult:
    """Try all active profiles against a filename and return the best match.

    Returns:
        MatchResult with status 'matched' (exactly one), 'multiple_matches',
        or 'unmatched' (zero).
    """
    if not filename or not filename.strip():
        return MatchResult(status="unmatched")

    successful_results: list[ParseResult] = []
    for profile in profiles:
        result = parse_filename(filename, profile)
        if result.success:
            successful_results.append(result)

    if len(successful_results) == 0:
        return MatchResult(status="unmatched")
    elif len(successful_results) == 1:
        return MatchResult(
            status="matched",
            parse_result=successful_results[0],
            candidate_profile_ids=[successful_results[0].profile_id] if successful_results[0].profile_id else [],
        )
    else:
        return MatchResult(
            status="multiple_matches",
            candidate_profile_ids=[r.profile_id for r in successful_results if r.profile_id],
            candidate_results=successful_results,
        )


async def resolve_entities(
    parse_result: ParseResult,
    profile: NamingProfile,
    org_id: int,
    db: AsyncSession,
) -> EntityResolution:
    """Look up project/experiment/sample from parsed segment values.

    Uses the profile's code mappings to resolve codes to entity IDs.
    Returns None for entities that can't be resolved (to be auto-created by ingest).
    """
    resolution = EntityResolution()
    segments = parse_result.segments

    # Resolve project
    project_code = segments.get("project_code")
    if project_code:
        resolution.project_name = project_code
        # Check code mappings first
        mapped_id = profile.project_code_mappings.get(project_code)
        if mapped_id:
            try:
                pid = int(mapped_id)
                result = await db.execute(select(Project).where(Project.id == pid, Project.organization_id == org_id))
                project = result.scalar_one_or_none()
                if project:
                    resolution.project_id = project.id
                    resolution.project_name = project.name
            except (ValueError, TypeError):
                pass

        # If not mapped, try to find by name
        if not resolution.project_id:
            result = await db.execute(
                select(Project).where(Project.name == project_code, Project.organization_id == org_id)
            )
            project = result.scalar_one_or_none()
            if project:
                resolution.project_id = project.id
                resolution.project_name = project.name

    # Resolve experiment
    experiment_code = segments.get("experiment_code")
    if experiment_code:
        resolution.experiment_name = experiment_code
        mapped_id = profile.experiment_code_mappings.get(experiment_code)
        if mapped_id:
            try:
                eid = int(mapped_id)
                result = await db.execute(
                    select(Experiment).where(Experiment.id == eid, Experiment.organization_id == org_id)
                )
                experiment = result.scalar_one_or_none()
                if experiment:
                    resolution.experiment_id = experiment.id
                    resolution.experiment_name = experiment.name
            except (ValueError, TypeError):
                pass

        if not resolution.experiment_id:
            result = await db.execute(
                select(Experiment).where(Experiment.name == experiment_code, Experiment.organization_id == org_id)
            )
            experiment = result.scalar_one_or_none()
            if experiment:
                resolution.experiment_id = experiment.id
                resolution.experiment_name = experiment.name

    # Resolve sample
    sample_id_ext = segments.get("sample_id")
    if sample_id_ext:
        resolution.sample_name = sample_id_ext
        result = await db.execute(select(Sample).where(Sample.sample_id_unique == sample_id_ext))
        sample = result.scalar_one_or_none()
        if sample:
            resolution.sample_id = sample.id
            resolution.sample_name = sample.sample_id_unique

    return resolution
