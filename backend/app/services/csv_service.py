import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.schemas.sample import SampleCreate

logger = logging.getLogger("bioaf.csv_service")

# All user-facing sample fields (excludes internal fields like id, status, experiment_id)
SAMPLE_FIELDS = [
    "external_id",
    "organism",
    "tissue_type",
    "donor_source",
    "treatment_condition",
    "chemistry_version",
    "viability_pct",
    "cell_count",
    "prep_notes",
    "molecule_type",
    "library_prep_method",
    "library_layout",
    "assay",
    "qc_status",
    "qc_notes",
    "collection_timestamp",
    "collection_method",
    "sample_batch",
    "sequencing_batch",
    "sequencing_batch_position",
]

# Maps common CSV header names to sample model field names
COLUMN_MAP = {
    "sample": "external_id",
    "sample_id": "external_id",
    "sample_id_external": "external_id",
    "external_id": "external_id",
    "organism": "organism",
    "tissue": "tissue_type",
    "tissue_type": "tissue_type",
    "donor": "donor_source",
    "donor_source": "donor_source",
    "treatment": "treatment_condition",
    "treatment_condition": "treatment_condition",
    "chemistry": "chemistry_version",
    "chemistry_version": "chemistry_version",
    "viability": "viability_pct",
    "viability_pct": "viability_pct",
    "cell_count": "cell_count",
    "prep_notes": "prep_notes",
    "notes": "prep_notes",
    "qc_status": "qc_status",
    "qc_notes": "qc_notes",
    "molecule_type": "molecule_type",
    "library_prep_method": "library_prep_method",
    "library_layout": "library_layout",
    "assay": "assay",
    "collection_timestamp": "collection_timestamp",
    "collection_method": "collection_method",
    "sample_batch": "sample_batch_code",
    "sample_batch_code": "sample_batch_code",
    "prep_batch": "sample_batch_code",
    "sequencing_batch": "sequencing_batch_code",
    "sequencing_batch_code": "sequencing_batch_code",
    "seq_batch": "sequencing_batch_code",
    "sequencing_batch_position": "sequencing_batch_position",
    "seq_batch_position": "sequencing_batch_position",
    "batch_position": "sequencing_batch_position",
}


@dataclass(frozen=True)
class ParsedSampleRow:
    """One accepted CSV record: the sample, its custom fields, and the line it came from.

    Kept together so a rejected record can never shift another record's custom fields.
    ``record_number`` is the CSV line (the header is line 1).
    """

    record_number: int
    sample: SampleCreate
    custom_fields: dict[str, Any] = field(default_factory=dict)


NUMERIC_FIELDS = {"viability_pct", "cell_count", "sequencing_batch_position"}
DATETIME_FIELDS = {"collection_timestamp"}

# Example values for the template CSV
_EXAMPLE_VALUES = {
    "external_id": "SAMPLE-001",
    "organism": "Homo sapiens",
    "tissue_type": "PBMC",
    "donor_source": "Donor-A",
    "treatment_condition": "Control",
    "chemistry_version": "v3.1",
    "viability_pct": "92.5",
    "cell_count": "10000",
    "prep_notes": "Standard protocol",
    "molecule_type": "total RNA",
    "library_prep_method": "10x Chromium 3' v3",
    "library_layout": "paired",
    "assay": "scrna",
    "qc_status": "pass",
    "qc_notes": "",
    "collection_timestamp": "2024-01-15T10:30:00",
    "collection_method": "venipuncture",
}


def _detect_encoding(content: bytes) -> str:
    for encoding in ["utf-8", "latin-1"]:
        try:
            content.decode(encoding)
            return encoding
        except (UnicodeDecodeError, ValueError):
            continue
    return "utf-8"


def _detect_delimiter(first_line: str) -> str:
    if "\t" in first_line:
        return "\t"
    return ","


def _normalize_header(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_")


def _build_header_map(
    fieldnames: list[str],
    column_mappings: dict[str, str] | None = None,
    aliases: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Build header-to-field mapping. Returns (header_map, unknown_columns).

    Lookup priority for an unrecognized header: user-provided ``column_mappings``
    win, then experiment-persisted ``aliases``, then the global ``COLUMN_MAP``.
    Both ``column_mappings`` and ``aliases`` are keyed by the normalized header.
    """
    header_map: dict[str, str] = {}
    unknown_columns: list[str] = []

    for raw_header in fieldnames:
        clean = _normalize_header(raw_header)

        if column_mappings and clean in column_mappings:
            header_map[raw_header] = column_mappings[clean]
            continue

        if aliases and clean in aliases:
            header_map[raw_header] = aliases[clean]
            continue

        if clean in COLUMN_MAP:
            header_map[raw_header] = COLUMN_MAP[clean]
        else:
            unknown_columns.append(clean)

    return header_map, unknown_columns


def _convert_value(field_name: str, raw_val: str) -> tuple[Any, str | None]:
    """Convert a raw string value to the appropriate type for the field.

    Returns (converted_value, error_message_or_none).
    """
    if field_name in NUMERIC_FIELDS:
        try:
            if field_name in ("cell_count", "sequencing_batch_position"):
                return int(raw_val), None
            return float(raw_val), None
        except ValueError:
            return None, f"Invalid numeric value '{raw_val}' for {field_name}"

    if field_name in DATETIME_FIELDS:
        try:
            return datetime.fromisoformat(raw_val), None
        except ValueError:
            return None, f"Invalid datetime value '{raw_val}' for {field_name}"

    return raw_val, None


def _parse_rows(
    text: str,
    delimiter: str,
    header_map: dict[str, str],
) -> tuple[list[tuple[int, dict[str, Any], dict[str, Any]]], list[str]]:
    """Parse CSV rows using the header map, skipping empty lines.

    Returns (records, errors). Each record is (csv_line, sample_fields, custom_fields):
    sample_fields maps field_name -> value (for SampleCreate) and custom_fields maps
    custom field name -> value for the same line.
    """
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    records: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    errors: list[str] = []

    for row_num, row in enumerate(reader, start=2):
        if all(not v or not v.strip() for v in row.values()):
            continue

        sample_data: dict[str, Any] = {}
        custom_fields: dict[str, Any] = {}

        for raw_header, field_name in header_map.items():
            val = row.get(raw_header, "").strip()
            if not val:
                continue

            if field_name.startswith("custom:"):
                custom_fields[field_name.removeprefix("custom:")] = val
                continue

            converted, error = _convert_value(field_name, val)
            if error:
                errors.append(f"Row {row_num}: {error}")
                continue
            sample_data[field_name] = converted

        records.append((row_num, sample_data, custom_fields))

    return records, errors


def generate_sample_template() -> bytes:
    """Generate a CSV template with all sample fields and an example row."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(SAMPLE_FIELDS)
    writer.writerow([_EXAMPLE_VALUES.get(f, "") for f in SAMPLE_FIELDS])
    return output.getvalue().encode("utf-8")


def preview_sample_csv(
    file_content: bytes,
    aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Parse a CSV file and return a preview without creating any samples.

    Returns a dict with:
        recognized_columns: list of {csv_header, mapped_to}
        unknown_columns: list of unrecognized header names
        preview_rows: first 5 rows of parsed data
        total_rows: total data row count
        errors: list of parse error messages
    """
    encoding = _detect_encoding(file_content)
    text = file_content.decode(encoding)

    lines = text.strip().splitlines()
    if not lines:
        return {
            "recognized_columns": [],
            "unknown_columns": [],
            "preview_rows": [],
            "total_rows": 0,
            "errors": ["File is empty"],
        }

    delimiter = _detect_delimiter(lines[0])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    if not reader.fieldnames:
        return {
            "recognized_columns": [],
            "unknown_columns": [],
            "preview_rows": [],
            "total_rows": 0,
            "errors": ["No header row found"],
        }

    header_map, unknown_columns = _build_header_map(list(reader.fieldnames), aliases=aliases)

    recognized_columns = [{"csv_header": raw, "mapped_to": field} for raw, field in header_map.items()]

    records, errors = _parse_rows(text, delimiter, header_map)
    total_rows = len(records)

    # Build preview rows (first 5) using mapped field names
    preview_rows = [sample_data for _, sample_data, _ in records[:5]]

    return {
        "recognized_columns": recognized_columns,
        "unknown_columns": unknown_columns,
        "preview_rows": preview_rows,
        "total_rows": total_rows,
        "errors": errors,
    }


def parse_sample_rows(
    file_content: bytes,
    column_mappings: dict[str, str] | None = None,
    aliases: dict[str, str] | None = None,
) -> tuple[list[ParsedSampleRow], list[str]]:
    """Parse a CSV file into accepted rows and error messages.

    Args:
        file_content: Raw CSV bytes.
        column_mappings: Optional user-provided mappings for unknown columns.
            Keys are normalized header names, values are either a sample field
            name (e.g. "prep_notes") or "custom:field_name" for custom fields.
        aliases: Optional experiment-persisted header->target mappings, used as
            a fallback when ``column_mappings`` does not cover a header.

    A record that fails SampleCreate validation is reported and dropped with its
    custom fields. A value that fails numeric or datetime conversion is reported
    and omitted; the rest of its record is still accepted.

    Error labels: conversion errors name the CSV line; validation errors number
    the non-empty records from 2, so the two differ after a skipped empty line.
    """
    encoding = _detect_encoding(file_content)
    text = file_content.decode(encoding)

    lines = text.strip().splitlines()
    if not lines:
        return [], ["File is empty"]

    delimiter = _detect_delimiter(lines[0])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    if not reader.fieldnames:
        return [], ["No header row found"]

    header_map, _ = _build_header_map(list(reader.fieldnames), column_mappings, aliases=aliases)

    records, errors = _parse_rows(text, delimiter, header_map)

    rows: list[ParsedSampleRow] = []
    for i, (record_number, sample_data, custom_fields) in enumerate(records):
        label = i + 2
        try:
            rows.append(ParsedSampleRow(record_number, SampleCreate(**sample_data), custom_fields))
        except ValidationError as ve:
            field_msgs = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in ve.errors())
            errors.append(f"Row {label}: {field_msgs}")
        except Exception:
            logger.exception("Unexpected error parsing CSV row %d", record_number)
            errors.append(f"Row {label}: could not be parsed")

    return rows, errors


def parse_sample_csv(
    file_content: bytes,
    experiment_id: int,
    column_mappings: dict[str, str] | None = None,
    aliases: dict[str, str] | None = None,
) -> tuple[list[SampleCreate], list[str], list[dict[str, Any]]]:
    """``parse_sample_rows`` as parallel lists: (samples, errors, custom_field_rows).

    ``custom_field_rows[i]`` belongs to ``samples[i]``.
    """
    rows, errors = parse_sample_rows(file_content, column_mappings, aliases)
    return [r.sample for r in rows], errors, [r.custom_fields for r in rows]
