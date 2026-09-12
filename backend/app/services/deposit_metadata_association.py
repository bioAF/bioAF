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
METADATA_ROLES = ("sample_id", "condition", "replicate", "batch", "sample_accession")

_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "sample_id": ("sample", "sampleid", "sample_name", "samplename", "name", "id", "title", "library"),
    # change_7.4 section 1.4: the repository accession a row describes. A design names GSMs, and a
    # column placed by its accession is the only kind a declared pairing can be carried onto.
    "sample_accession": ("geoaccession", "gsm", "gsmid", "sampleaccession", "accession"),
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
        row = {
            "sample_id": sample_id,
            "condition": _cell("condition"),
            "replicate": _cell("replicate"),
            "batch": _cell("batch"),
        }
        # Only a table that states an accession carries one; the row is otherwise unchanged.
        if idx["sample_accession"] is not None:
            row["sample_accession"] = _cell("sample_accession")
        rows.append(row)
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
    # the accession, which occasionally IS the column name. change_7.5 section 1.7: the GSM too, which
    # is what a paper's design names.
    by_manifest: dict[str, dict] = {}
    for m in manifest or []:
        for key in (m.get("title"), m.get("geo_accession"), m.get("experiment_accession"), m.get("sample_accession")):
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
                    "sample_accession": meta.get("sample_accession"),
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
        # change_7.5 section 1.7: an exact identifier match is authoritative, whether or not the
        # repository records a condition: the column IS that sample, named by its title or its GSM.
        if man:
            accessions = [
                str(a)
                for a in (man.get("geo_accession"), man.get("experiment_accession"), man.get("sample_accession"))
                if a
            ]
            rows.append(
                {
                    "column": col,
                    "sample_accession": accessions[0] if accessions else None,
                    "accessions": accessions,
                    "condition": man.get("condition") or None,
                    "replicate": None,
                    "batch": None,
                    "source": "series_matrix",
                    "reason": "the column is this sample in the GEO series matrix, matched exactly on its "
                    + ("accession" if col in accessions else "title"),
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


# The sources that STATE what a column is. A column name read by pattern is an inference.
_AUTHORITATIVE_SOURCES = ("metadata_file", "series_matrix")


def empty_arm_cause(associations: list[dict]) -> str:
    """Why an arm came out empty, decided by the evidence behind the columns.

    change_7.4 section 1.4: study 37 read ``KO Cl16`` as condition "KO Cl", replicate 16, from the
    name alone, and then reported that no column matched "SAMD1 KO". When every column is placed by a
    source that states it (a metadata file, or the repository's sample manifest) and none lands in
    the arm, the input really lacks that condition: ``design_incompatible``. When any column rests on
    its name alone, or on nothing, the mapping is what failed: ``sample_mapping_unresolved``.
    """
    from app.services.validation_acquisition_outcome import DESIGN_INCOMPATIBLE, SAMPLE_MAPPING_UNRESOLVED

    rows = [a for a in associations or [] if a.get("column")]
    if rows and all(a.get("source") in _AUTHORITATIVE_SOURCES for a in rows):
        return DESIGN_INCOMPATIBLE
    return SAMPLE_MAPPING_UNRESOLVED


def rewrite_design_to_columns(
    design: dict, associations: list[dict], *, contrast_index: int | None
) -> tuple[dict, str, str | None]:
    """Rewrite the SELECTED contrast's arms to the matrix's own column names.

    Returns ``(design, status, reason)`` where status is ``"ok"``, ``"mismatch"`` (an arm resolved
    to nothing) or ``"pairing_lost"`` (a declared pairing could not be carried onto the columns).
    Mirrors ``_resolve_sample_design`` on the pipeline route, including its contract: an arm that
    resolves to nothing HOLDS rather than launching, because a contrast with an empty arm is not a
    smaller experiment, it is not an experiment.

    change_7.4 section 1.4: only the selected contrast is validated and rewritten. Study 37 was
    refused for the day-7 contrast's arms, which the selected matrix never held and nothing had
    selected. The other contrasts stay in the plan untouched. A declared pairing is carried onto the
    column names, exactly as the raw-reads route does; where a label cannot be carried the run stops,
    because running a paired design unpaired is a different analysis that nothing would record.

    A pick is matched to columns by the accession the association carries or by the column's own
    name, and only then, for the whole arm, by CONDITION: a deposited matrix rarely names GSMs in its
    columns, and matching on the condition is what makes a column-named matrix usable at all. A
    column matched by condition cannot say which sample it was, so it cannot carry a pairing.
    """
    contrasts = (design or {}).get("contrasts") or []
    if not contrasts or not isinstance(contrast_index, int) or not 0 <= contrast_index < len(contrasts):
        return design or {}, "ok", None

    by_pick: dict[str, list[str]] = {}
    by_condition: dict[str, list[str]] = {}
    for a in associations or []:
        col = a.get("column")
        if not col:
            continue
        # A pick that names the column itself is the plainest identity there is.
        by_pick.setdefault(str(col).strip().lower(), []).append(col)
        for accession in {a.get("sample_accession"), *(a.get("accessions") or [])} - {None, ""}:
            by_pick.setdefault(str(accession).strip().lower(), []).append(col)
        if a.get("condition"):
            by_condition.setdefault(str(a["condition"]).strip().lower(), []).append(col)

    def _resolve_arm(picks: list[str] | None, condition: str | None) -> tuple[list[str], dict[str, str]]:
        out: list[str] = []
        carried_from: dict[str, str] = {}
        for pick in picks or []:
            for col in by_pick.get(str(pick).strip().lower(), []):
                if col not in out:
                    out.append(col)
                    carried_from[col] = pick
        if not out and condition:
            out = list(by_condition.get(str(condition).strip().lower(), []))
        return out, carried_from

    c = contrasts[contrast_index]
    test, test_from = _resolve_arm(c.get("test_samples"), c.get("test_condition"))
    reference, reference_from = _resolve_arm(c.get("reference_samples"), c.get("reference_condition"))
    empty_arms: list[str] = []
    if not test:
        empty_arms.append(str(c.get("test_condition") or c.get("name") or "test"))
    if not reference:
        empty_arms.append(str(c.get("reference_condition") or c.get("name") or "reference"))

    rewritten_contrast = {**c, "test_samples": test, "reference_samples": reference}
    lost: list[str] = []
    subjects = c.get("subjects") or {}
    if subjects:
        carried_from = {**test_from, **reference_from}
        new_subjects: dict[str, str] = {}
        for col in test + reference:
            pick = carried_from.get(col)
            label = subjects.get(pick) if pick is not None else None
            if label is None:
                lost.append(col)
            else:
                new_subjects[col] = label
        rewritten_contrast["subjects"] = new_subjects

    new_contrasts = list(contrasts)
    new_contrasts[contrast_index] = rewritten_contrast
    rewritten = {**(design or {}), "contrasts": new_contrasts}
    if empty_arms:
        return (
            rewritten,
            "mismatch",
            "Held before running: no column of the deposited matrix could be matched to "
            f"{'; '.join(sorted(set(empty_arms)))}. An arm with no samples is not a smaller "
            "experiment.",
        )
    if lost:
        return (
            rewritten,
            "pairing_lost",
            f"Held before running: {c.get('name') or 'the selected contrast'} is a paired design, and the pairing "
            f"could not be carried onto the deposited matrix's columns ({', '.join(lost)} carry no identifier that "
            "places them in a pair). A paired design is never run unpaired.",
        )
    return rewritten, "ok", None
