"""plan_8_3 stage 4: one versioned reading of a deposited matrix's columns, shared by every consumer.

Several readers independently assumed the first column is the feature identifier and every column
after it is a sample. Study 50's deposit begins `GeneType`, `GeneSymbol`, `ENSG` and then its 21
measurement columns, so bioAF reported 23 samples, gave two annotation columns zero library sizes,
refused the sample mapping because two of its "samples" were unassigned, and would have sent a gene
biotype into the analysis template as the gene identifier. Excluding the two apparent samples alone
would still have sent the wrong identifier.

**Roles come from evidence, in order of what settles them.** A column the repository's own sample
records name is a measurement. A column whose header is one of the words annotation columns are
spelled with is an annotation. A column holding no numeric observations is not a measurement. What is
left, and numeric, is a measurement. Numeric content alone never makes a column a sample: coordinates,
lengths and identifiers are numbers too.

**The feature identifier is the column that identifies a row**, which is a property of its values, not
of its position: among the columns that are not measurements, the one whose values are largely
distinct, preferring a recognised identifier namespace. A biotype repeated across thousands of rows
cannot identify a row and is never chosen. Where nothing identifies a row, that is unresolved, not a
guess.

**Nothing is repaired quietly.** Duplicate identifiers, blank identifiers, missing cells, nonfinite
cells and a column with no numeric observation at all are each counted and reported. Identifier text
is preserved exactly, versions and all; any stripping, namespace mapping or duplicate aggregation
would be a recorded transformation, and this module performs none.

A model may PROPOSE a reading; this module checks it against the whole input and records where the two
disagree. The content wins, because the content is the thing being analysed.
"""

from __future__ import annotations

import math
import re

INTERPRETATION_VERSION = 1

FEATURE_ID = "feature_id"
ANNOTATION = "annotation"
MEASUREMENT = "measurement"

# Roles a column can be given, and what established each.
BASIS_REPOSITORY = "repository_record"
BASIS_ANNOTATION_NAME = "annotation_name"
BASIS_NO_OBSERVATIONS = "no_numeric_observations"
BASIS_NUMERIC = "numeric_measurements"
BASIS_DISTINCT_IDENTIFIERS = "distinct_identifiers"

# Why a matrix has no usable reading. Each sends whoever reads the report to a different remedy.
UNRESOLVED_NO_ROWS = "no_rows"
UNRESOLVED_FEATURE_ID = "feature_id"
UNRESOLVED_MEASUREMENTS = "measurements"
UNRESOLVED_DUPLICATE_COLUMNS = "duplicate_columns"
UNRESOLVED_ORIENTATION = "orientation"

ORIENTATION_FEATURES_IN_ROWS = "features_in_rows"
ORIENTATION_SAMPLES_IN_ROWS = "samples_in_rows"

# Column sums within this fraction of 1e6 mean the matrix is per-million normalized (TPM or CPM).
_PER_MILLION_TOLERANCE = 0.05
_PER_MILLION = 1_000_000.0

# Of a column's non-empty cells, how many must parse as a number for the column to hold measurements.
# Below this a column is text: a handful of numeric-looking labels in a name column is not data.
_NUMERIC_FRACTION = 0.9
# Of a candidate's rows, how many must carry their own value for it to identify a row. Real deposits
# repeat and omit identifiers, so this is not 1.0; a category column sits orders of magnitude below it.
_DISTINCT_FRACTION = 0.5

# The words deposits spell annotation columns with, reduced to letters and digits. A column named one
# of these is an annotation whatever its values look like, which is what keeps a numeric gene length
# or an Entrez identifier out of the sample set.
_ANNOTATION_WORDS = frozenset(
    {
        "chromosome",
        "chr",
        "chrom",
        "seqname",
        "seqnames",
        "start",
        "end",
        "stop",
        "strand",
        "length",
        "genelength",
        "exonlength",
        "effectivelength",
        "width",
        "biotype",
        "genebiotype",
        "genetype",
        "transcriptbiotype",
        "symbol",
        "genesymbol",
        "genename",
        "hgncsymbol",
        "mgisymbol",
        "description",
        "entrez",
        "entrezid",
        "entrezgeneid",
        "band",
        "locus",
        "source",
        "feature",
        "gccontent",
        "gc",
    }
)

# The namespaces bioAF recognises for a feature identifier, best first. A recognised namespace beats
# an unrecognised one when two columns are both distinct enough to identify a row.
_NAMESPACES: tuple[tuple[str, re.Pattern], ...] = (
    ("ensembl_gene", re.compile(r"^ENS[A-Z]*G\d{6,}")),
    ("ensembl_transcript", re.compile(r"^ENS[A-Z]*T\d{6,}")),
    ("refseq", re.compile(r"^(?:N|X)[MRP]_\d+")),
    ("mirbase", re.compile(r"^(?:hsa|mmu|rno|dre|cel|dme)-(?:let|mir|miR)-", re.I)),
)
_NAMESPACE_RANK = {name: i for i, (name, _) in enumerate(_NAMESPACES)}
_UNRECOGNISED_RANK = len(_NAMESPACES)


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


# What a deposit writes where it has no value. A cell holding one of these is a MISSING observation,
# not text: a column of real numbers with three NAs in it is still a column of measurements.
# `nan` is deliberately absent: R writes NA where there is no observation and NaN where an arithmetic
# result is not a number, and the two need different remedies.
_MISSING_MARKERS = frozenset({"na", "n/a", "null", "none", "nd", "n.d.", ".", "-", "--", "?"})

CELL_VALUE = "value"
CELL_MISSING = "missing"
CELL_NONFINITE = "nonfinite"
CELL_TEXT = "text"


def _to_float(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _cell(raw: str) -> tuple[str, float | None]:
    """What one cell holds: a finite number, a missing observation, a nonfinite one, or text."""
    text = str(raw or "").strip()
    if text == "":
        return CELL_MISSING, None
    if text.lower() in _MISSING_MARKERS:
        return CELL_MISSING, None
    number = _to_float(text)
    if number is None:
        return CELL_TEXT, None
    if not math.isfinite(number):
        return CELL_NONFINITE, None
    return CELL_VALUE, number


def _sniff(line: str) -> tuple[str, str]:
    """``(delimiter, format)``: a tab where there is one, else a comma."""
    return ("\t", "tsv") if "\t" in line else (",", "csv")


def _namespace_of(values: list[str]) -> str:
    sample = [v for v in values[:400] if v]
    if not sample:
        return "unknown"
    for name, pattern in _NAMESPACES:
        if sum(1 for v in sample if pattern.match(v)) > len(sample) * 0.5:
            return name
    if sum(1 for v in sample if re.fullmatch(r"\d+", v)) > len(sample) * 0.5:
        return "entrez"
    return "unknown"


def _identifier_rank(values: list[str]) -> int:
    namespace = _namespace_of(values)
    return _NAMESPACE_RANK.get(namespace, _UNRECOGNISED_RANK)


def _unresolved(kind: str, reason: str, **extra) -> dict:
    return {
        "version": INTERPRETATION_VERSION,
        "status": "unresolved",
        "unresolved_kind": kind,
        "reason": reason,
        "source_checksum": None,
        "format": None,
        "orientation": None,
        "feature_column": None,
        "feature_index": None,
        "feature_namespace": None,
        "columns": [],
        "sample_columns": [],
        "annotation_columns": [],
        "columns_without_observations": [],
        "library_sizes": {},
        "library_size_ratio": None,
        "zero_columns": [],
        "zero_row_fraction": 0.0,
        "value_type": "unknown",
        "value_type_claimed": None,
        "value_type_disagrees": False,
        "n_rows": 0,
        "duplicate_feature_ids": [],
        "rows_without_feature_id": 0,
        "missing_cells": 0,
        "nonfinite_cells": 0,
        "transformations": [],
        "notes": [],
        **extra,
    }


def _record_identifiers(sample_records: list[dict] | None) -> set[str]:
    """Every exact identifier the repository's sample records name."""
    found: set[str] = set()
    for record in sample_records or []:
        for key in ("title", "geo_accession", "run_accession", "sample_accession", "experiment_accession"):
            value = str(record.get(key) or "").strip()
            if value:
                found.add(value)
    return found


def interpret_matrix(
    text: str,
    *,
    sample_records: list[dict] | None = None,
    sample_names: list[str] | None = None,
    claimed_value_type: str | None = None,
    source_checksum: str | None = None,
    proposal: dict | None = None,
) -> dict:
    """What each column of a deposited matrix is, measured from the whole input. Never raises.

    ``sample_records`` are the repository's own records for the deposit. They are the strongest
    evidence a column is a measurement, and they are not required: a preview is read before the
    records are scoped to the experiment, so the header and the content have to settle it alone.

    ``sample_names`` are further identifiers already established as samples (a design's own sample
    names, for instance), treated as the records are.

    ``proposal`` is a reading a model offered. It is checked, never trusted: a disagreement is recorded
    in ``notes`` and the measured reading stands.
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return _unresolved(UNRESOLVED_NO_ROWS, "the deposited table has no data rows")

    delimiter, fmt = _sniff(lines[0])
    header = [c.strip().strip('"') for c in lines[0].split(delimiter)]
    if len(header) < 2:
        return _unresolved(
            UNRESOLVED_MEASUREMENTS, "the deposited table has only one column, so it holds no measurements"
        )
    repeated = sorted({c for c in header if header.count(c) > 1 and c != ""})
    if repeated:
        return _unresolved(
            UNRESOLVED_DUPLICATE_COLUMNS,
            f"the deposited table names {', '.join(repeated)} more than once, so which column a value belongs to "
            "is not established",
        )

    cells: list[list[str]] = [[] for _ in header]
    n_rows = 0
    for line in lines[1:]:
        row = [c.strip().strip('"') for c in line.split(delimiter)]
        if len(row) < 2:
            continue
        n_rows += 1
        for i in range(len(header)):
            cells[i].append(row[i] if i < len(row) else "")
    if n_rows == 0:
        return _unresolved(UNRESOLVED_NO_ROWS, "the deposited table has no data rows")

    linked = _record_identifiers(sample_records) | {str(n).strip() for n in sample_names or [] if str(n).strip()}
    measurements: list[int] = []
    annotations: list[int] = []
    text_columns: list[int] = []
    without_observations: list[int] = []
    basis: dict[int, str] = {}

    for i, name in enumerate(header):
        kinds = [_cell(v)[0] for v in cells[i]]
        stated = [k for k in kinds if k != CELL_MISSING]
        observed = [k for k in kinds if k in (CELL_VALUE, CELL_NONFINITE)]
        # A column of numbers with missing cells in it is still a column of measurements; a column of
        # words is not, however many of its cells are blank.
        numeric = bool(observed) and len(observed) >= len(stated) * _NUMERIC_FRACTION
        if name in linked:
            # The repository names this column as one of its samples. A column it names that holds no
            # numeric observation at all is still not a measurement, and is reported as such.
            if numeric:
                measurements.append(i)
                basis[i] = BASIS_REPOSITORY
            else:
                without_observations.append(i)
                basis[i] = BASIS_NO_OBSERVATIONS
            continue
        if _squash(name) in _ANNOTATION_WORDS:
            annotations.append(i)
            basis[i] = BASIS_ANNOTATION_NAME
            if not numeric:
                text_columns.append(i)
            continue
        if not numeric:
            text_columns.append(i)
            annotations.append(i)
            basis[i] = BASIS_NO_OBSERVATIONS
            continue
        measurements.append(i)
        basis[i] = BASIS_NUMERIC

    # The feature identifier identifies a row, which is a property of its values. Among the columns
    # that are not measurements, the ones whose values are largely distinct are candidates; a
    # recognised namespace decides between them, then distinctness, then position.
    candidates = []
    for i in text_columns:
        values = cells[i]
        distinct = len({v for v in values if v != ""})
        # At least two distinct values wherever there are two rows to distinguish: a two-row table
        # whose one text column repeats a single word identifies nothing, and half of two is one.
        if distinct >= max(min(2, n_rows), n_rows * _DISTINCT_FRACTION):
            candidates.append((_identifier_rank(values), -distinct, i))
    if not candidates:
        return _unresolved(
            UNRESOLVED_FEATURE_ID,
            "no column of the deposited table holds an identifier for each row, so which feature a row measures "
            "is not established",
        )
    feature_index = sorted(candidates)[0][2]
    if feature_index in annotations:
        annotations.remove(feature_index)
    if not measurements:
        return _unresolved(
            UNRESOLVED_MEASUREMENTS,
            "no column of the deposited table holds numeric measurements, so there is nothing to analyse",
        )

    notes: list[str] = []
    proposed = (proposal or {}).get("feature_column")
    if proposed is not None and str(proposed) != header[feature_index]:
        notes.append(
            f"the proposed identifier column, {proposed}, does not identify a row of this table; "
            f"{header[feature_index] or 'the unnamed first column'} does"
        )

    ids = cells[feature_index]
    seen: dict[str, int] = {}
    for value in ids:
        if value != "":
            seen[value] = seen.get(value, 0) + 1
    duplicates = sorted(v for v, n in seen.items() if n > 1)
    blanks = sum(1 for v in ids if v == "")

    library_sizes: dict[str, float] = {}
    missing = 0
    nonfinite = 0
    saw_negative = False
    saw_fractional = False
    zero_rows = 0
    row_values: list[list[float]] = [[] for _ in range(n_rows)]
    for i in measurements:
        total = 0.0
        observations = 0
        for r, raw in enumerate(cells[i]):
            kind, value = _cell(raw)
            if kind == CELL_MISSING:
                missing += 1
                continue
            if kind != CELL_VALUE:
                # A nonfinite cell and a cell of words are both refused, and neither becomes a zero.
                nonfinite += 1
                continue
            observations += 1
            total += value
            row_values[r].append(value)
            if value < 0:
                saw_negative = True
            elif value != int(value):
                saw_fractional = True
        library_sizes[header[i]] = round(total, 6)
        # A sample whose every gene reads zero is a real measurement; a column with no observation at
        # all is not, and the two need different remedies.
        if observations == 0:
            without_observations.append(i)
    for values in row_values:
        if values and not any(v != 0 for v in values):
            zero_rows += 1

    positive = [s for s in library_sizes.values() if s > 0]
    ratio = round(max(positive) / min(positive), 6) if len(positive) > 1 else None
    # Order matters. A negative rules out counts AND per-million before either is considered, and
    # per-million is the specific case, so it is checked before "some other normalization".
    if saw_negative:
        value_type = "log_transformed"
    elif positive and all(abs(s - _PER_MILLION) / _PER_MILLION <= _PER_MILLION_TOLERANCE for s in positive):
        value_type = "tpm_or_cpm"
    elif saw_fractional:
        value_type = "normalized_other"
    else:
        value_type = "counts"
    claimed = (claimed_value_type or "").strip().lower()
    disagrees = bool(claimed) and claimed != "unknown" and not compatible_value_type(claimed, value_type)

    # A matrix whose ROWS are samples. Reading it as it stands would treat a handful of features as
    # the whole sample set. It is detected the only way it can be: the repository's records name the
    # row identifiers rather than the column headers.
    orientation = ORIENTATION_FEATURES_IN_ROWS
    if linked and not measurements_named(header, measurements, linked) and any(v in linked for v in ids):
        orientation = ORIENTATION_SAMPLES_IN_ROWS
        return _unresolved(
            UNRESOLVED_ORIENTATION,
            "the deposited matrix appears to be transposed (samples in rows, features in columns)",
            orientation=orientation,
            format=fmt,
            source_checksum=source_checksum,
        )

    columns = [
        {
            "name": header[i],
            "index": i,
            "role": FEATURE_ID
            if i == feature_index
            else MEASUREMENT
            if i in measurements
            else ANNOTATION
            if i in annotations
            else ANNOTATION,
            "basis": BASIS_DISTINCT_IDENTIFIERS if i == feature_index else basis.get(i, BASIS_NO_OBSERVATIONS),
        }
        for i in range(len(header))
    ]
    return {
        "version": INTERPRETATION_VERSION,
        "status": "established",
        "unresolved_kind": None,
        "reason": None,
        "source_checksum": source_checksum,
        "format": fmt,
        "orientation": orientation,
        "feature_column": header[feature_index],
        "feature_index": feature_index,
        "feature_namespace": _namespace_of(ids),
        "columns": columns,
        "sample_columns": [header[i] for i in measurements],
        "annotation_columns": [header[i] for i in range(len(header)) if i != feature_index and i not in measurements],
        "columns_without_observations": [header[i] for i in without_observations],
        "library_sizes": library_sizes,
        "library_size_ratio": ratio,
        "zero_columns": [c for c, s in library_sizes.items() if s == 0],
        "zero_row_fraction": zero_rows / n_rows,
        "value_type": value_type,
        "value_type_claimed": claimed or None,
        "value_type_disagrees": disagrees,
        "n_rows": n_rows,
        "duplicate_feature_ids": duplicates,
        "rows_without_feature_id": blanks,
        "missing_cells": missing,
        "nonfinite_cells": nonfinite,
        # Nothing here rewrites the deposit. A stripped version, a namespace mapping or a duplicate
        # aggregation would each be recorded as a transformation with its rule and its provenance.
        "transformations": [],
        "notes": notes,
    }


def measurements_named(header: list[str], measurements: list[int], linked: set[str]) -> bool:
    """Whether any column the repository named ended up as a measurement."""
    return any(header[i] in linked for i in measurements)


def compatible_value_type(claimed: str, observed: str) -> bool:
    """Whether a claimed value type is consistent with what was measured.

    `tpm` and `cpm` both measure as `tpm_or_cpm`, so neither claim is a disagreement: the matrix
    genuinely cannot tell them apart and pretending otherwise would manufacture a conflict.
    """
    if claimed == observed:
        return True
    return observed == "tpm_or_cpm" and claimed in ("tpm", "cpm")
