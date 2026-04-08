"""Manifest file parser for sequencing batch file delivery.

Supports md5sum and CSV formats. Returns structured results with
batch number and list of (filename, md5) entries.
"""

import csv
import io
from dataclasses import dataclass, field


@dataclass
class ManifestFileEntry:
    filename: str
    md5: str


@dataclass
class ManifestParseResult:
    batch_number: str | None = None
    entries: list[ManifestFileEntry] = field(default_factory=list)


def parse_manifest(content: str, format: str) -> ManifestParseResult:
    """Parse a manifest file and return structured results.

    Args:
        content: Raw text content of the manifest file.
        format: Parser format -- "md5sum" or "csv".

    Returns:
        ManifestParseResult with batch_number and list of entries.

    Raises:
        ValueError: If format is not supported.
    """
    if format in ("md5sum", "txt"):
        return _parse_md5sum(content)
    elif format == "csv":
        return _parse_csv(content)
    else:
        raise ValueError(f"Unsupported manifest format: {format}")


def _parse_md5sum(content: str) -> ManifestParseResult:
    """Parse md5sum-style manifest.

    First line may be a batch header comment: # batch: <batch_number>
    Remaining lines: <md5>  <filename> (two-space separator)
    """
    result = ManifestParseResult()

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Check for batch header comment
        if stripped.startswith("#"):
            if stripped.lower().startswith("# batch:"):
                result.batch_number = stripped.split(":", 1)[1].strip()
            continue

        # Parse md5sum line: <hash>  <filename>
        if "  " not in stripped:
            continue

        parts = stripped.split("  ", 1)
        if len(parts) != 2:
            continue

        md5, filename = parts[0].strip(), parts[1].strip()
        if md5 and filename:
            result.entries.append(ManifestFileEntry(filename=filename, md5=md5))

    return result


def _parse_csv(content: str) -> ManifestParseResult:
    """Parse CSV manifest with columns: batch_number, filename, md5."""
    result = ManifestParseResult()

    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        if not any(row.values()):
            continue

        filename = row.get("filename", "").strip()
        md5 = row.get("md5", "").strip()
        batch_number = row.get("batch_number", "").strip()

        if batch_number and not result.batch_number:
            result.batch_number = batch_number

        if filename and md5:
            result.entries.append(ManifestFileEntry(filename=filename, md5=md5))

    return result
