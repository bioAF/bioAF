"""GEO export validation engine — checks completeness of metadata for GEO submission."""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger("bioaf.geo.validation")

TEMPLATE_PATH = Path(__file__).parent / "geo_template_definition.json"


class FieldValidation(BaseModel):
    """Validation result for a single GEO field."""

    geo_column: str
    status: str  # "complete", "populated_unvalidated", "missing_required", "missing_recommended"
    value: str | None = None
    message: str | None = None


class SampleValidation(BaseModel):
    """Validation results for a single sample."""

    sample_id: int
    sample_name: str
    fields: list[FieldValidation]


class FileCheckStatus(BaseModel):
    """Status of a file's checksum."""

    filename: str
    has_checksum: bool
    gcs_uri: str | None = None


class FileManifestStatus(BaseModel):
    """Overall file manifest validation."""

    total_files: int
    files_with_checksums: int
    files_missing_checksums: int
    files: list[FileCheckStatus]


class ValidationSummary(BaseModel):
    """Summary counts of validation results."""

    total_fields: int
    complete: int
    populated_unvalidated: int
    missing_required: int
    missing_recommended: int


class ValidationReport(BaseModel):
    """Complete validation report for a GEO export."""

    experiment_id: int
    pipeline_run_id: int | None
    series_fields: list[FieldValidation]
    sample_validations: list[SampleValidation]
    protocol_fields: list[FieldValidation]
    file_manifest: FileManifestStatus
    summary: ValidationSummary


def _load_template() -> dict:
    """Load the GEO template definition JSON."""
    with open(TEMPLATE_PATH) as f:
        return json.load(f)


def _validate_field(
    geo_column: str,
    value: Any,
    required: bool,
    recommended: bool,
    controlled_vocab: list[str] | None,
) -> FieldValidation:
    """Validate a single field value against GEO requirements."""
    str_value = str(value) if value is not None else None

    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            return FieldValidation(
                geo_column=geo_column,
                status="missing_required",
                message=f"Required field '{geo_column}' is missing",
            )
        elif recommended:
            return FieldValidation(
                geo_column=geo_column,
                status="missing_recommended",
                message=f"Recommended field '{geo_column}' is missing",
            )
        else:
            return FieldValidation(
                geo_column=geo_column,
                status="missing_recommended",
                value=None,
            )

    if controlled_vocab and str_value not in controlled_vocab:
        return FieldValidation(
            geo_column=geo_column,
            status="populated_unvalidated",
            value=str_value,
            message=f"Value '{str_value}' not in GEO controlled vocabulary",
        )

    return FieldValidation(
        geo_column=geo_column,
        status="complete",
        value=str_value,
    )


def _derive_value(
    derivation: str,
    derivation_rules: dict[str, str] | None,
    source_value: str | None,
    experiment_data: dict,
    sample_data: dict | None,
    pipeline_data: dict | None,
) -> str | None:
    """Compute derived GEO field values."""
    if derivation in ("library_strategy", "library_source", "library_selection"):
        if not derivation_rules or not source_value:
            return derivation_rules.get("default") if derivation_rules else None
        return derivation_rules.get(source_value, derivation_rules.get("default"))

    if derivation == "sample_title":
        if not sample_data:
            return None
        parts = [
            sample_data.get("sample_id_external", ""),
            sample_data.get("tissue_type", ""),
            sample_data.get("treatment_condition", ""),
        ]
        return " - ".join(p for p in parts if p)

    if derivation == "overall_design":
        samples = experiment_data.get("samples", [])
        organisms = set()
        tissues = set()
        for s in samples:
            if s.get("organism"):
                organisms.add(s["organism"])
            if s.get("tissue_type"):
                tissues.add(s["tissue_type"])
        parts = [f"{len(samples)} samples"]
        if organisms:
            parts.append(f"organism: {', '.join(sorted(organisms))}")
        if tissues:
            parts.append(f"tissue: {', '.join(sorted(tissues))}")
        return "; ".join(parts)

    if derivation == "concatenate":
        desc = experiment_data.get("description", "") or ""
        hyp = experiment_data.get("hypothesis", "") or ""
        parts = [p for p in [desc, hyp] if p.strip()]
        return " ".join(parts) if parts else None

    if derivation == "data_processing":
        if not pipeline_data:
            return None
        parts = []
        if pipeline_data.get("pipeline_name"):
            parts.append(pipeline_data["pipeline_name"])
        if pipeline_data.get("pipeline_version"):
            parts.append(f"v{pipeline_data['pipeline_version']}")
        if pipeline_data.get("alignment_algorithm"):
            parts.append(f"alignment: {pipeline_data['alignment_algorithm']}")
        if pipeline_data.get("reference_genome"):
            parts.append(f"reference: {pipeline_data['reference_genome']}")
        return "; ".join(parts) if parts else None

    if derivation == "library_construction":
        if not sample_data:
            return None
        parts = []
        if sample_data.get("library_prep_method"):
            parts.append(sample_data["library_prep_method"])
        if sample_data.get("chemistry_version"):
            parts.append(sample_data["chemistry_version"])
        return ", ".join(parts) if parts else None

    if derivation == "data_processing_steps":
        if not pipeline_data:
            return None
        steps = []
        if pipeline_data.get("pipeline_name"):
            steps.append(f"Pipeline: {pipeline_data['pipeline_name']} {pipeline_data.get('pipeline_version', '')}")
        if pipeline_data.get("alignment_algorithm"):
            steps.append(f"Alignment: {pipeline_data['alignment_algorithm']}")
        if pipeline_data.get("reference_genome"):
            steps.append(f"Reference genome: {pipeline_data['reference_genome']}")
        return "; ".join(steps) if steps else None

    return None


def _get_source_value(
    bioaf_source: str,
    experiment_data: dict,
    sample_data: dict | None,
    pipeline_data: dict | None,
    files_data: dict | None,
) -> Any:
    """Resolve a bioAF source path to a value."""
    parts = bioaf_source.split(".")

    if parts[0] == "experiment":
        obj = experiment_data
    elif parts[0] == "sample":
        obj = sample_data or {}
    elif parts[0] in ("batch", "sample_batch"):
        obj = sample_data.get("sample_batch") or {} if sample_data else {}
    elif parts[0] == "sequencing_batch":
        obj = sample_data.get("sequencing_batch") or {} if sample_data else {}
    elif parts[0] == "pipeline_run":
        obj = pipeline_data or {}
    elif parts[0] == "files":
        # Prefer per-sample file data when resolving in a sample context
        if sample_data and "files" in sample_data:
            obj = sample_data["files"]
        else:
            obj = files_data or {}
    else:
        return None

    for key in parts[1:]:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
        if obj is None:
            return None

    return obj


def validate_experiment_for_geo(
    experiment_data: dict,
    samples_data: list[dict],
    pipeline_data: dict | None,
    files_data: dict | None,
) -> ValidationReport:
    """Run full GEO validation on experiment data.

    Args:
        experiment_data: Experiment-level data dict.
        samples_data: List of sample data dicts (with batch info nested).
        pipeline_data: Pipeline run data dict (optional).
        files_data: Files data dict with raw_filenames, processed_filenames, etc.
    """
    template = _load_template()
    all_fields: list[FieldValidation] = []

    # SERIES validation
    series_fields: list[FieldValidation] = []
    for field_def in template["sections"]["SERIES"]["fields"]:
        geo_col = field_def["geo_column"]
        required = field_def.get("required", False)
        recommended = field_def.get("recommended", False)
        vocab = field_def.get("controlled_vocabulary")
        derivation = field_def.get("derivation")

        if derivation:
            value = _derive_value(
                derivation,
                field_def.get("derivation_rules"),
                None,
                experiment_data,
                None,
                pipeline_data,
            )
        elif field_def["bioaf_source"] == "derived":
            value = None
        else:
            value = _get_source_value(
                field_def["bioaf_source"],
                experiment_data,
                None,
                pipeline_data,
                files_data,
            )

        result = _validate_field(geo_col, value, required, recommended, vocab)
        series_fields.append(result)
        all_fields.append(result)

    # SAMPLES validation
    sample_validations: list[SampleValidation] = []
    for sample in samples_data:
        sample_fields: list[FieldValidation] = []
        for field_def in template["sections"]["SAMPLES"]["fields"]:
            geo_col = field_def["geo_column"]
            required = field_def.get("required", False)
            recommended = field_def.get("recommended", False)
            vocab = field_def.get("controlled_vocabulary")
            derivation = field_def.get("derivation")

            if derivation:
                # For library_strategy/source/selection, source value is from sample
                source_val = None
                if derivation == "library_strategy":
                    source_val = sample.get("library_prep_method")
                elif derivation == "library_source":
                    source_val = sample.get("molecule_type")
                elif derivation == "library_selection":
                    source_val = sample.get("library_prep_method")

                value = _derive_value(
                    derivation,
                    field_def.get("derivation_rules"),
                    source_val,
                    experiment_data,
                    sample,
                    pipeline_data,
                )
            elif field_def["bioaf_source"] == "derived":
                value = None
            else:
                value = _get_source_value(
                    field_def["bioaf_source"],
                    experiment_data,
                    sample,
                    pipeline_data,
                    files_data,
                )

            result = _validate_field(geo_col, value, required, recommended, vocab)
            sample_fields.append(result)
            all_fields.append(result)

        sample_validations.append(
            SampleValidation(
                sample_id=sample.get("id", 0),
                sample_name=sample.get("sample_id_external", "unknown"),
                fields=sample_fields,
            )
        )

    # PROTOCOLS validation
    protocol_fields: list[FieldValidation] = []
    # Use first sample for protocol derivation since library construction is sample-level
    first_sample = samples_data[0] if samples_data else None
    for field_def in template["sections"]["PROTOCOLS"]["fields"]:
        geo_col = field_def["geo_column"]
        required = field_def.get("required", False)
        recommended = field_def.get("recommended", False)
        vocab = field_def.get("controlled_vocabulary")
        derivation = field_def.get("derivation")

        if derivation:
            value = _derive_value(
                derivation,
                field_def.get("derivation_rules"),
                first_sample.get("library_prep_method") if first_sample else None,
                experiment_data,
                first_sample,
                pipeline_data,
            )
        elif field_def["bioaf_source"] == "derived":
            value = None
        else:
            value = _get_source_value(
                field_def["bioaf_source"],
                experiment_data,
                first_sample,
                pipeline_data,
                files_data,
            )

        result = _validate_field(geo_col, value, required, recommended, vocab)
        protocol_fields.append(result)
        all_fields.append(result)

    # File manifest validation
    file_checks: list[FileCheckStatus] = []
    raw_files = (files_data or {}).get("raw_files", [])
    processed_files = (files_data or {}).get("processed_files", [])
    all_file_entries = raw_files + processed_files

    for f in all_file_entries:
        file_checks.append(
            FileCheckStatus(
                filename=f.get("filename", "unknown"),
                has_checksum=bool(f.get("md5_checksum")),
                gcs_uri=f.get("gcs_uri"),
            )
        )

    file_manifest = FileManifestStatus(
        total_files=len(file_checks),
        files_with_checksums=sum(1 for f in file_checks if f.has_checksum),
        files_missing_checksums=sum(1 for f in file_checks if not f.has_checksum),
        files=file_checks,
    )

    # Summary
    summary = ValidationSummary(
        total_fields=len(all_fields),
        complete=sum(1 for f in all_fields if f.status == "complete"),
        populated_unvalidated=sum(1 for f in all_fields if f.status == "populated_unvalidated"),
        missing_required=sum(1 for f in all_fields if f.status == "missing_required"),
        missing_recommended=sum(1 for f in all_fields if f.status == "missing_recommended"),
    )

    return ValidationReport(
        experiment_id=experiment_data.get("id", 0),
        pipeline_run_id=pipeline_data.get("id") if pipeline_data else None,
        series_fields=series_fields,
        sample_validations=sample_validations,
        protocol_fields=protocol_fields,
        file_manifest=file_manifest,
        summary=summary,
    )
