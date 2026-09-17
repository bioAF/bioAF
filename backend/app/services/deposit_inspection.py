"""plan_7 step 6: check the deposited matrix is what we expect, before using it.

On the pipeline route, MultiQC is what stands between a run and its verdict. A deposited matrix
arrives with no QC at all, so without this it would go straight into a differential test and the
first sign of trouble would be a number nobody could explain.

**Pure measurement. No model.** Step 2 lets the model STATE a `value_type` from the filename; this
module MEASURES it from the numbers, and where they disagree the measurement wins. That precedence
is the point: a file named ``counts.tsv`` whose columns each sum to 1e6 is a CPM table whatever
anyone called it, and handing it to DESeq2 invalidates the dispersion model and produces numbers
that are confidently wrong rather than obviously wrong.

The numbers here are not only a gate. They land in the evidence bundle and on the provenance report,
because a matrix with a 40x library-size spread is a real observation about the deposit and belongs
in the verdict rather than in a log.

**plan_8_3 stage 4: which column is which is not decided here.** ``matrix_interpretation`` establishes
it once, from the header, the content and the repository's sample records, and every consumer reads
that one reading. This module measures the matrix THROUGH it, so an annotation column can no longer
arrive as a sample with a zero library size.
"""

from __future__ import annotations

import logging

from app.services.matrix_interpretation import interpret_matrix
from app.services.result_set_normalizer import _detect_namespace

logger = logging.getLogger("bioaf.deposit_inspection")


def _unusable(reason: str, *, interpretation: dict | None = None, **extra) -> dict:
    """A table that could not be read as a matrix at all. change_7.4 section 1.1: its content was not
    identified, which is ``input_unreadable``."""
    from app.services.validation_acquisition_outcome import INPUT_UNREADABLE

    return {
        "n_rows": (interpretation or {}).get("n_rows") or 0,
        "n_columns": 0,
        "columns": [],
        "annotation_columns": [],
        "id_column": None,
        "id_namespace": None,
        "value_type_observed": "unknown",
        "value_type_disagrees": False,
        "library_sizes": {},
        "library_size_ratio": None,
        "zero_row_fraction": 0.0,
        "zero_columns": [],
        "columns_without_observations": [],
        "design_samples_found": 0,
        "design_samples_missing": [],
        "looks_transposed": False,
        "usable": False,
        "unusable_reason": reason,
        "unusable_cause": INPUT_UNREADABLE,
        "interpretation": interpretation,
        **extra,
    }


def inspect_matrix(
    text: str,
    *,
    claimed_value_type: str | None = None,
    design_samples: list[str] | None = None,
    sample_records: list[dict] | None = None,
    source_checksum: str | None = None,
    gate_on_coverage: bool = True,
) -> dict:
    """Measure a deposited matrix: which column is which, what its values are, how even its libraries
    are, and whether the study's design can actually be run on it.

    ``gate_on_coverage=False`` still MEASURES how many design samples were found, but does not let a
    zero make the matrix unusable. The deposit route needs that: its design names GSM accessions
    while the matrix names its own columns, and bridging the two is exactly what step 7's association
    does. Gating here would refuse every deposit before the thing that resolves it had run. A
    TRANSPOSED matrix still gates either way, because no association can fix an axis swap.

    Never raises. An unreadable table returns ``usable: False`` with a reason, because that is a fact
    about the deposit that a scientist can act on (fix the selection, or escalate to raw reads).
    """
    from app.services.matrix_interpretation import (
        UNRESOLVED_DUPLICATE_COLUMNS,
        UNRESOLVED_FEATURE_ID,
        UNRESOLVED_ORIENTATION,
    )
    from app.services.validation_acquisition_outcome import SAMPLE_MAPPING_UNRESOLVED, UNSUPPORTED_PROCESSING

    interpretation = interpret_matrix(
        text,
        sample_records=sample_records,
        sample_names=design_samples,
        claimed_value_type=claimed_value_type,
        source_checksum=source_checksum,
    )
    if interpretation["status"] != "established":
        reason = interpretation["reason"] or "the deposited table could not be read as a matrix"
        cause = None
        if interpretation["unresolved_kind"] in (UNRESOLVED_ORIENTATION, UNRESOLVED_FEATURE_ID):
            cause = UNSUPPORTED_PROCESSING
        elif interpretation["unresolved_kind"] == UNRESOLVED_DUPLICATE_COLUMNS:
            cause = SAMPLE_MAPPING_UNRESOLVED
        out = _unusable(reason, interpretation=interpretation)
        if cause:
            out["unusable_cause"] = cause
        out["looks_transposed"] = interpretation["unresolved_kind"] == UNRESOLVED_ORIENTATION
        return out

    sample_columns = list(interpretation["sample_columns"])
    ids_namespace = interpretation["feature_namespace"]
    wanted = [s for s in (design_samples or []) if s]
    found = [s for s in wanted if s in sample_columns]
    missing = [s for s in wanted if s not in sample_columns]

    usable, reason, cause = True, None, None
    # change_7.4 section 1.1: a matrix read and identified as a shape bioAF cannot analyze is
    # `unsupported_processing`; one whose columns cannot be placed is an unresolved mapping.
    if len(sample_columns) < 2:
        usable, reason = False, "the deposited matrix has only one sample column, so it cannot carry a contrast"
        cause = UNSUPPORTED_PROCESSING
    elif gate_on_coverage and wanted and not found:
        usable, reason = (
            False,
            f"none of the study's design samples appear in the deposited matrix "
            f"(looked for {', '.join(wanted[:5])}; the matrix has {', '.join(sample_columns[:5])})",
        )
        cause = SAMPLE_MAPPING_UNRESOLVED

    return {
        "n_rows": interpretation["n_rows"],
        "n_columns": len(sample_columns),
        "columns": sample_columns,
        # plan_8_3 stage 4: what the matrix holds BESIDES its measurements, so no reader has to guess
        # again and no annotation is reported as an excluded experimental sample.
        "annotation_columns": list(interpretation["annotation_columns"]),
        "id_column": interpretation["feature_column"],
        # `_detect_namespace` is what the ground-truth route uses, so the two agree about what an
        # identifier IS; the interpretation's own stricter reading is on `interpretation`.
        "id_namespace": _detect_namespace(_feature_values(text, interpretation)) if ids_namespace else None,
        "value_type_observed": interpretation["value_type"],
        "value_type_claimed": interpretation["value_type_claimed"],
        "value_type_disagrees": interpretation["value_type_disagrees"],
        "library_sizes": interpretation["library_sizes"],
        "library_size_ratio": interpretation["library_size_ratio"],
        "zero_row_fraction": interpretation["zero_row_fraction"],
        "zero_columns": interpretation["zero_columns"],
        "columns_without_observations": interpretation["columns_without_observations"],
        "design_samples_found": len(found),
        "design_samples_missing": missing,
        "looks_transposed": False,
        "usable": usable,
        "unusable_reason": reason,
        "unusable_cause": cause,
        "duplicate_feature_ids": interpretation["duplicate_feature_ids"],
        "rows_without_feature_id": interpretation["rows_without_feature_id"],
        "interpretation": {k: v for k, v in interpretation.items() if k not in ("library_sizes", "columns")},
    }


def _feature_values(text: str, interpretation: dict) -> list[str]:
    """The identifier column's values, read through the established interpretation."""
    index = interpretation.get("feature_index")
    if index is None:
        return []
    delimiter = "\t" if interpretation.get("format") == "tsv" else ","
    values = []
    for line in [ln for ln in (text or "").splitlines() if ln.strip()][1:]:
        cells = line.split(delimiter)
        if index < len(cells):
            values.append(cells[index].strip().strip('"'))
    return values
