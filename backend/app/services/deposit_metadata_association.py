"""plan_7 step 7: associate sample metadata with the deposited matrix's columns.

The longest of the bioinformaticians' requirements, and the hardest:

    The metadata should be available for download. It will need to be associated manually by the LLM.
    Sometimes it will need to infer from the sample / file name. Other times it will need to read the
    GEO description and content. Other times the metadata has it, but the headers may be incorrect.

That is three sources, in the order they name them, and the ordering is a precedence:

1. **The downloaded metadata file.** Its headers may be wrong, so it goes through the same
   model-decides / person-picks seam ``column_resolution`` gives result tables (a55fe889).
2. **The GEO series matrix** (``!Sample_title`` + ``!Sample_characteristics_ch1``), which
   ``accession_manifest_service`` already fetches and currently spends only on rendering the picker.
3. **The column names themselves**, when neither of the above resolves.

**Every association carries its source.** A condition the depositor wrote down and one a model
inferred from a filename are both usable, and they are not the same strength of evidence. The gate
renders them differently, and a verdict argued from an inferred grouping should be readable as such.

The rewrite at the end mirrors ``_resolve_sample_design`` on the pipeline route: the design's arms
become the matrix's own column names, so the differential test matches its input by construction.
Same held-before-compute contract, too: a design that maps no columns HOLDS rather than running a
contrast with an empty arm.
"""

from __future__ import annotations

import logging
import re

from app.services.result_set_normalizer import _sniff_delim, _squash

logger = logging.getLogger("bioaf.deposit_metadata_association")

# What a sample-metadata table needs. Registered as a `column_resolution` kind so the "headers may be
# incorrect" case reaches the same seam result tables use.
METADATA_ROLES = ("sample_id", "condition", "replicate", "batch")

_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "sample_id": ("sample", "sampleid", "sample_name", "samplename", "name", "id", "title", "library"),
    "condition": ("condition", "group", "treatment", "genotype", "status", "arm", "phenotype", "class"),
    "replicate": ("replicate", "rep", "biologicalreplicate", "biorep", "repnum"),
    "batch": ("batch", "run", "lane", "block"),
}

# A trailing replicate marker: `Control-KD_1`, `WT.2`, `sample-3`. The part before it is the
# condition and the number is the replicate.
_REPLICATE_SUFFIX = re.compile(r"^(?P<stem>.+?)[._\- ]?(?P<rep>\d+)$")

# Inference from a column name is a guess from a string. Usable, and deliberately never as strong as
# a source that stated the answer.
_INFERENCE_CONFIDENCE = 0.55


def _index_of(header: list[str], names: tuple[str, ...], column_map: dict | None, role: str) -> int | None:
    """The column index for ``role``: the caller's map first, then the alias list.

    A mapped name the header does not have is IGNORED rather than honoured, exactly as
    ``result_set_normalizer._mapped`` does, so a wrong hint can never blank a table the aliases would
    have parsed on their own.
    """
    squashed = [_squash(h) for h in header]
    if column_map:
        target = _squash(str(column_map.get(role) or ""))
        if target and target in squashed:
            return squashed.index(target)
    for n in names:
        if n in squashed:
            return squashed.index(n)
    return None


def parse_metadata_table(text: str, *, column_map: dict | None = None) -> list[dict]:
    """Rows of a deposited sample-metadata table, keyed by our roles. Never raises."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    delim = _sniff_delim(lines[0])
    header = [c.strip() for c in lines[0].split(delim)]
    if len(header) < 2:
        return []

    idx = {role: _index_of(header, _ROLE_ALIASES[role], column_map, role) for role in METADATA_ROLES}
    if idx["sample_id"] is None:
        return []

    rows: list[dict] = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(delim)]

        def _cell(role: str) -> str | None:
            i = idx[role]
            if i is None or i >= len(cells):
                return None
            return cells[i] or None

        sample_id = _cell("sample_id")
        if not sample_id:
            continue
        rows.append(
            {
                "sample_id": sample_id,
                "condition": _cell("condition"),
                "replicate": _cell("replicate"),
                "batch": _cell("batch"),
            }
        )
    return rows


def infer_from_column_names(columns: list[str]) -> list[dict]:
    """Group the matrix's columns by the convention in their own names.

    This is the bioinformaticians' "sometimes it will need to infer from the sample / file name", and
    it is the common case: `Control-KD_1..3` / `H2AS40-KD_1..3` on GSE274331 carries the whole design
    with no metadata file at all.

    Returns [] unless the names produce at least TWO groups. One group is not a contrast, and
    inventing a grouping out of unrelated names would manufacture a comparison the paper never ran.
    """
    parsed: list[tuple[str, str, str | None]] = []
    for col in columns or []:
        m = _REPLICATE_SUFFIX.match(col.strip())
        if m:
            parsed.append((col, m.group("stem").rstrip("._- "), m.group("rep")))
        else:
            parsed.append((col, col.strip(), None))

    groups = {stem for _, stem, _ in parsed}
    if len(groups) < 2:
        return []

    return [
        {
            "column": col,
            "sample_accession": None,
            "condition": stem,
            "replicate": rep,
            "batch": None,
            "source": "column_name",
            "reason": f"grouped from the column name: '{col}' reads as condition '{stem}'"
            + (f", replicate {rep}" if rep else ""),
            "confidence": _INFERENCE_CONFIDENCE,
        }
        for col, stem, rep in parsed
    ]


def associate_columns(
    columns: list[str],
    *,
    metadata_rows: list[dict] | None = None,
    manifest: list[dict] | None = None,
) -> list[dict]:
    """One row per matrix column, saying what that column is and where we learned it.

    Precedence is the order the bioinformaticians named: a stated metadata file beats the series
    matrix, which beats an inference from the column's own name. A column nothing resolves is still
    returned, marked ``unresolved``: staying silent about it would read as "there is no such column",
    when what the gate needs to show is a column that is present and unexplained.
    """
    cols = [c for c in (columns or []) if c]
    by_meta = {r["sample_id"]: r for r in (metadata_rows or []) if r.get("sample_id")}

    # Match the series matrix on TITLE first (what a depositor names a column after) and fall back to
    # the accession, which occasionally IS the column name.
    by_manifest: dict[str, dict] = {}
    for m in manifest or []:
        for key in (m.get("title"), m.get("experiment_accession"), m.get("sample_accession")):
            if key:
                by_manifest.setdefault(str(key), m)

    inferred = {r["column"]: r for r in infer_from_column_names(cols)}

    rows: list[dict] = []
    for col in cols:
        meta = by_meta.get(col)
        if meta and meta.get("condition"):
            rows.append(
                {
                    "column": col,
                    "sample_accession": None,
                    "condition": meta.get("condition"),
                    "replicate": meta.get("replicate"),
                    "batch": meta.get("batch"),
                    "source": "metadata_file",
                    "reason": "stated in the deposited sample metadata table",
                    "confidence": 1.0,
                }
            )
            continue

        man = by_manifest.get(col)
        if man and man.get("condition"):
            rows.append(
                {
                    "column": col,
                    "sample_accession": man.get("experiment_accession") or man.get("sample_accession"),
                    "condition": man.get("condition"),
                    "replicate": None,
                    "batch": None,
                    "source": "series_matrix",
                    "reason": "stated by the depositor in the GEO series matrix",
                    "confidence": 1.0,
                }
            )
            continue

        if col in inferred:
            rows.append(inferred[col])
            continue

        rows.append(
            {
                "column": col,
                "sample_accession": None,
                "condition": None,
                "replicate": None,
                "batch": None,
                "source": "unresolved",
                "reason": "no metadata file, series-matrix entry or name convention explains this column",
                "confidence": 0.0,
            }
        )
    return rows


def rewrite_design_to_columns(design: dict, associations: list[dict]) -> tuple[dict, str, str | None]:
    """Rewrite the design's arms to the matrix's own column names.

    Returns ``(design, "ok", None)`` or ``(design, "mismatch", reason)``. Mirrors
    ``_resolve_sample_design`` on the pipeline route, including its contract: an arm that resolves to
    nothing HOLDS rather than launching, because a contrast with an empty arm is not a smaller
    experiment, it is not an experiment.

    Arms are matched by accession first, then by CONDITION, because a deposited matrix rarely names
    GSMs in its columns and matching on the condition is what makes a column-named matrix usable.
    """
    contrasts = (design or {}).get("contrasts") or []
    if not contrasts:
        return design or {}, "ok", None

    by_accession: dict[str, list[str]] = {}
    by_condition: dict[str, list[str]] = {}
    for a in associations or []:
        col = a.get("column")
        if not col:
            continue
        if a.get("sample_accession"):
            by_accession.setdefault(str(a["sample_accession"]).strip().lower(), []).append(col)
        if a.get("condition"):
            by_condition.setdefault(str(a["condition"]).strip().lower(), []).append(col)

    def _resolve_arm(picks: list[str] | None, condition: str | None) -> list[str]:
        out: list[str] = []
        for pick in picks or []:
            for col in by_accession.get(str(pick).strip().lower(), []):
                if col not in out:
                    out.append(col)
        if not out and condition:
            out = list(by_condition.get(str(condition).strip().lower(), []))
        return out

    new_contrasts = []
    empty_arms: list[str] = []
    for c in contrasts:
        test = _resolve_arm(c.get("test_samples"), c.get("test_condition"))
        reference = _resolve_arm(c.get("reference_samples"), c.get("reference_condition"))
        if not test:
            empty_arms.append(str(c.get("test_condition") or c.get("name") or "test"))
        if not reference:
            empty_arms.append(str(c.get("reference_condition") or c.get("name") or "reference"))
        new_contrasts.append({**c, "test_samples": test, "reference_samples": reference})

    rewritten = {**(design or {}), "contrasts": new_contrasts}
    if empty_arms:
        return (
            rewritten,
            "mismatch",
            "Held before running: no column of the deposited matrix could be matched to "
            f"{'; '.join(sorted(set(empty_arms)))}. An arm with no samples is not a smaller "
            "experiment.",
        )
    return rewritten, "ok", None
