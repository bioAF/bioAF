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
"""

from __future__ import annotations

import logging

from app.services.result_set_normalizer import _detect_namespace, _sniff_delim

logger = logging.getLogger("bioaf.deposit_inspection")

# Column sums within this fraction of 1e6 mean the matrix is per-million normalized (TPM or CPM).
# Generous, because a deposit is often rounded or filtered after normalization and no longer sums
# exactly.
_PER_MILLION_TOLERANCE = 0.05
_PER_MILLION = 1_000_000.0


def _to_float(v: str) -> float | None:
    try:
        return float((v or "").strip())
    except (TypeError, ValueError):
        return None


def _unusable(reason: str, **extra) -> dict:
    return {
        "n_rows": 0,
        "n_columns": 0,
        "columns": [],
        "id_column": None,
        "id_namespace": None,
        "value_type_observed": "unknown",
        "value_type_disagrees": False,
        "library_sizes": {},
        "library_size_ratio": None,
        "zero_row_fraction": 0.0,
        "zero_columns": [],
        "design_samples_found": 0,
        "design_samples_missing": [],
        "looks_transposed": False,
        "usable": False,
        "unusable_reason": reason,
        **extra,
    }


def inspect_matrix(
    text: str,
    *,
    claimed_value_type: str | None = None,
    design_samples: list[str] | None = None,
    gate_on_coverage: bool = True,
) -> dict:
    """Measure a deposited matrix: shape, what its values are, how even its libraries are, and
    whether the study's design can actually be run on it.

    ``gate_on_coverage=False`` still MEASURES how many design samples were found, but does not let a
    zero make the matrix unusable. The deposit route needs that: its design names GSM accessions
    while the matrix names its own columns, and bridging the two is exactly what step 7's association
    does. Gating here would refuse every deposit before the thing that resolves it had run. A
    TRANSPOSED matrix still gates either way, because no association can fix an axis swap.

    Never raises. An unreadable table returns ``usable: False`` with a reason, because that is a fact
    about the deposit that a scientist can act on (fix the selection, or escalate to raw reads).
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return _unusable("the deposited table has no data rows")

    delim = _sniff_delim(lines[0])
    header = [c.strip() for c in lines[0].split(delim)]
    if len(header) < 2:
        return _unusable("the deposited table has only one column, so it holds no samples")

    # An EMPTY first header cell is the unnamed-index convention and is the common shape in real
    # deposits: GSE274331's TPM table is exactly this. `result_set_normalizer` already treats an
    # empty header cell as the index, so the two agree.
    id_column, sample_columns = header[0], header[1:]

    ids: list[str] = []
    columns: dict[str, list[float]] = {c: [] for c in sample_columns}
    zero_rows = 0
    n_rows = 0
    saw_negative = False
    saw_fractional = False

    for line in lines[1:]:
        cells = [c.strip() for c in line.split(delim)]
        if len(cells) < 2:
            continue
        n_rows += 1
        ids.append(cells[0])
        row_values: list[float] = []
        for i, col in enumerate(sample_columns, start=1):
            v = _to_float(cells[i]) if i < len(cells) else None
            if v is None:
                continue
            columns[col].append(v)
            row_values.append(v)
            if v < 0:
                saw_negative = True
            elif v != int(v):
                saw_fractional = True
        if row_values and not any(v != 0 for v in row_values):
            zero_rows += 1

    if n_rows == 0:
        return _unusable("the deposited table has no data rows")

    library_sizes = {c: round(sum(v), 6) for c, v in columns.items()}
    positive = [s for s in library_sizes.values() if s > 0]
    ratio = round(max(positive) / min(positive), 6) if len(positive) > 1 else None

    # Order matters. A negative rules out counts AND per-million before either is considered, and
    # per-million is checked before "some other normalization" because it is the specific case.
    if saw_negative:
        value_type = "log_transformed"
    elif positive and all(abs(s - _PER_MILLION) / _PER_MILLION <= _PER_MILLION_TOLERANCE for s in positive):
        # TPM and CPM are indistinguishable from the matrix alone: both sum to 1e6 per column. The
        # distinction only matters for interpretation, not for which test to run, so it is left
        # honestly unresolved rather than guessed.
        value_type = "tpm_or_cpm"
    elif saw_fractional:
        value_type = "normalized_other"
    else:
        value_type = "counts"

    claimed = (claimed_value_type or "").strip().lower()
    disagrees = bool(claimed) and claimed != "unknown" and not _compatible(claimed, value_type)

    wanted = [s for s in (design_samples or []) if s]
    found = [s for s in wanted if s in columns]
    missing = [s for s in wanted if s not in columns]

    # A matrix whose COLUMNS are genes and whose ROWS are samples. Reading it as-is would treat a
    # handful of genes as the whole sample set and analyse nothing. Detected by the design: if the
    # design's samples appear among the row IDS rather than the column headers, it is the wrong way
    # round.
    id_set = set(ids)
    looks_transposed = bool(wanted) and not found and sum(1 for s in wanted if s in id_set) > 0

    usable = True
    reason = None
    if len(sample_columns) < 2:
        usable, reason = False, "the deposited matrix has only one sample column, so it cannot carry a contrast"
    elif looks_transposed:
        usable, reason = False, "the deposited matrix appears to be transposed (samples in rows, features in columns)"
    elif gate_on_coverage and wanted and not found:
        usable, reason = (
            False,
            f"none of the study's design samples appear in the deposited matrix "
            f"(looked for {', '.join(wanted[:5])}; the matrix has {', '.join(sample_columns[:5])})",
        )

    return {
        "n_rows": n_rows,
        "n_columns": len(sample_columns),
        "columns": sample_columns,
        "id_column": id_column,
        "id_namespace": _detect_namespace(ids),
        "value_type_observed": value_type,
        "value_type_claimed": claimed or None,
        "value_type_disagrees": disagrees,
        "library_sizes": library_sizes,
        "library_size_ratio": ratio,
        "zero_row_fraction": zero_rows / n_rows,
        "zero_columns": [c for c, s in library_sizes.items() if s == 0],
        "design_samples_found": len(found),
        "design_samples_missing": missing,
        "looks_transposed": looks_transposed,
        "usable": usable,
        "unusable_reason": reason,
    }


def _compatible(claimed: str, observed: str) -> bool:
    """Whether a claimed value type is consistent with what was measured.

    `tpm` and `cpm` both measure as `tpm_or_cpm`, so neither claim is a disagreement: the matrix
    genuinely cannot tell them apart and pretending otherwise would manufacture a conflict.
    """
    if claimed == observed:
        return True
    return observed == "tpm_or_cpm" and claimed in ("tpm", "cpm")
