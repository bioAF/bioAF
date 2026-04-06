import csv
import io
from datetime import datetime
from typing import Any

from app.schemas.sample import SampleCreate

# All user-facing sample fields (excludes internal fields like id, status, experiment_id)
SAMPLE_FIELDS = [
    "sample_id_external",
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
    "qc_status",
    "qc_notes",
    "collection_timestamp",
    "collection_method",
    "sample_batch",
    "sequencing_batch",
]

# Maps common CSV header names to sample model field names
COLUMN_MAP = {
    "sample_id": "sample_id_external",
    "external_id": "sample_id_external",
    "sample_id_external": "sample_id_external",
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
    "collection_timestamp": "collection_timestamp",
    "collection_method": "collection_method",
    "sample_batch": "sample_batch_code",
    "sample_batch_code": "sample_batch_code",
    "prep_batch": "sample_batch_code",
    "sequencing_batch": "sequencing_batch_code",
    "sequencing_batch_code": "sequencing_batch_code",
    "seq_batch": "sequencing_batch_code",
}

NUMERIC_FIELDS = {"viability_pct", "cell_count"}
DATETIME_FIELDS = {"collection_timestamp"}

# Example values for the template CSV
_EXAMPLE_VALUES = {
    "sample_id_external": "SAMPLE-001",
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
) -> tuple[dict[str, str], list[str]]:
    """Build header-to-field mapping. Returns (header_map, unknown_columns)."""
    header_map: dict[str, str] = {}
    unknown_columns: list[str] = []

    for raw_header in fieldnames:
        clean = _normalize_header(raw_header)

        # Check user-provided mappings first
        if column_mappings and clean in column_mappings:
            target = column_mappings[clean]
            if target.startswith("custom:"):
                # Custom field -- store as-is, handled by caller
                header_map[raw_header] = target
            else:
                header_map[raw_header] = target
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
            if field_name == "cell_count":
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], int]:
    """Parse CSV rows using the header map.

    Returns (sample_dicts, custom_field_rows, errors, total_rows).
    Each sample_dict maps field_name -> value (for SampleCreate).
    Each custom_field_row maps custom_field_name -> value (keyed by row index).
    """
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    sample_dicts: list[dict[str, Any]] = []
    custom_field_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    total_rows = 0

    for row_num, row in enumerate(reader, start=2):
        if all(not v or not v.strip() for v in row.values()):
            continue

        total_rows += 1
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

        sample_dicts.append(sample_data)
        custom_field_rows.append(custom_fields)

    return sample_dicts, custom_field_rows, errors, total_rows


def generate_sample_template() -> bytes:
    """Generate a CSV template with all sample fields and an example row."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(SAMPLE_FIELDS)
    writer.writerow([_EXAMPLE_VALUES.get(f, "") for f in SAMPLE_FIELDS])
    return output.getvalue().encode("utf-8")


def preview_sample_csv(file_content: bytes) -> dict[str, Any]:
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

    header_map, unknown_columns = _build_header_map(list(reader.fieldnames))

    recognized_columns = [{"csv_header": raw, "mapped_to": field} for raw, field in header_map.items()]

    sample_dicts, _, errors, total_rows = _parse_rows(text, delimiter, header_map)

    # Build preview rows (first 5) using mapped field names
    preview_rows = []
    for sample_data in sample_dicts[:5]:
        preview_rows.append(sample_data)

    return {
        "recognized_columns": recognized_columns,
        "unknown_columns": unknown_columns,
        "preview_rows": preview_rows,
        "total_rows": total_rows,
        "errors": errors,
    }


def parse_sample_csv(
    file_content: bytes,
    experiment_id: int,
    column_mappings: dict[str, str] | None = None,
) -> tuple[list[SampleCreate], list[str], list[dict[str, Any]]]:
    """Parse a CSV file into SampleCreate objects.

    Args:
        file_content: Raw CSV bytes.
        experiment_id: The experiment to associate samples with.
        column_mappings: Optional user-provided mappings for unknown columns.
            Keys are normalized header names, values are either a sample field
            name (e.g. "prep_notes") or "custom:field_name" for custom fields.

    Returns:
        (samples, errors, custom_field_rows) where custom_field_rows is a list
        of dicts parallel to samples, mapping custom field names to values.
    """
    encoding = _detect_encoding(file_content)
    text = file_content.decode(encoding)

    lines = text.strip().splitlines()
    if not lines:
        return [], ["File is empty"], []

    delimiter = _detect_delimiter(lines[0])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    if not reader.fieldnames:
        return [], ["No header row found"], []

    header_map, _ = _build_header_map(list(reader.fieldnames), column_mappings)

    sample_dicts, custom_field_rows, errors, _ = _parse_rows(text, delimiter, header_map)

    samples = []
    for i, sample_data in enumerate(sample_dicts):
        try:
            sample = SampleCreate(**sample_data)
            samples.append(sample)
        except Exception as e:
            row_num = i + 2  # CSV row numbers start at 2 (after header)
            errors.append(f"Row {row_num}: {str(e)}")
            # Remove corresponding custom fields row so indexes stay aligned
            if i < len(custom_field_rows):
                custom_field_rows[i] = {}

    return samples, errors, custom_field_rows
