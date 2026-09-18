"""change_7.5 section 4.1 (7.4 section 3.1 carried forward): consistency with the authors' results.

The authors' own result table (a deposited `de_table` or `da_table`, or a supplement whose role is
`results_table`) is checked against the claim at the claim's own predicate, with the one function that
applies a predicate everywhere (`validation_predicate.count_passing`), then compared with the claim's
count relation. Consistency is not execution: it never raises the attempt fact.

**A table's conventions need evidence; numbers alone never establish them** (7.4 section 2.4):

- the effect scale (log2 or linear) is established by the column's own header, a legend or README,
  the methods, or a recorded confirmation;
- the ratio orientation (test over reference, or the reverse) likewise, and the contrast's own arm
  names describe the claim, never the table;
- a headerless table's column roles likewise.

Value ranges can only rule a convention out (a column with negative values is not a linear fold
change; values outside 0 to 1 are not a P value). A check that depends on an unestablished convention
is `unresolved`, with the candidate interpretations shown; a check that does not depend on it proceeds.

The record holds the table and the columns used, the predicate with its assumptions, the counting rules,
rows tested, passing and missing, conflicting duplicates, the claim's value and relation, and the
outcome `agree | disagree | unresolved | not_checkable` with the reason.
"""

from __future__ import annotations

import csv
import io
import re

from app.services.validation_predicate import FAILS, HOLDS, count_passing, evaluate_count

AGREE = "agree"
DISAGREE = "disagree"
UNRESOLVED = "unresolved"
NOT_CHECKABLE = "not_checkable"

# plan_8_3 sections 0.1 and 1.2: the version of the consistency READING itself.
#
# A comparison made while a table's bytes were in hand is kept and reused by the queued check, so a
# repair to what the reading establishes does not reach a study that already has a record. Study 55's
# 88-gene refinement stayed "the claim states no significance cutoff" for exactly that reason, on a
# build that could now reach the subset operation. The version is a dependency of every check record,
# so a record made under an earlier reading is superseded and run again, and a held record from an
# earlier reading is never reused as this build's answer.
#
# 1: the deployed reading, which recorded no version at all.
# 2: a refinement linked to the list it refines across a passage's sentences (section 1.2).
CONSISTENCY_VERSION = 2

_LOG_WORDS = ("log2", "logfc", "log fc", "log2fc", "logfoldchange", "log fold", "logratio")
_LINEAR_WORDS = ("foldchange", "fold change", "fold_change", "fc")
_ID_NAMES = ("gene", "gene_id", "geneid", "gene_symbol", "symbol", "gene_name", "id", "ensembl", "feature", "")
_PVAL_NAMES = ("pvalue", "p.value", "pval", "p_val", "p value")
_PADJ_NAMES = ("padj", "adj.p.val", "fdr", "qvalue", "q.value", "p.adjust", "padjust", "adjusted p", "p_val_adj")


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _number(value: str) -> float | None:
    text = (value or "").strip().strip('"')
    if text.upper() in ("", "NA", "NAN", "NULL", "-", "NONE"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _rows(text: str) -> list[list[str]]:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return []
    delimiter = "\t" if "\t" in lines[0] else ","
    return [
        [c.strip().strip('"') for c in row] for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    ]


def _first(header: list[str], names: tuple[str, ...]) -> int | None:
    squashed = [_squash(h) for h in header]
    for name in names:
        if _squash(name) in squashed:
            return squashed.index(_squash(name))
    return None


def _headerless(rows: list[list[str]]) -> bool:
    """A first row whose cells after the first are all numbers is data, not a header."""
    first = rows[0] if rows else []
    return len(first) > 1 and all(_number(c) is not None for c in first[1:])


def _candidate_roles(rows: list[list[str]]) -> dict:
    """What each column COULD be, from values that can only rule a role out."""
    width = max((len(r) for r in rows), default=0)
    roles = {"id": [], "lfc": [], "pvalue": [], "padj": []}
    for i in range(width):
        values = [r[i] for r in rows if i < len(r)]
        numbers = [_number(v) for v in values]
        present = [n for n in numbers if n is not None]
        if not present:
            roles["id"].append(i)
            continue
        roles["lfc"].append(i)
        if all(0 <= n <= 1 for n in present):
            roles["pvalue"].append(i)
            roles["padj"].append(i)
    return roles


def read_table(
    text: str,
    *,
    contrast_name: str | None = None,
    interpretation: dict | None = None,
    selector: dict | None = None,
) -> dict:
    """The table's rows keyed by role, the columns used, and what its header establishes.

    plan_8_2 section 1.1: ``selector`` is a binding's per-contrast columns in a table that pools several
    contrasts (``{"lfc": 3, "padj": 4}``). Those columns are read, and a statistic the selector does not
    name is not taken from another contrast's columns.

    Returns ``{"rows": [{"id", "lfc", "pvalue", "padj"}], "columns": {...}, "headerless": bool,
    "candidate_roles": {...} | None, "scale": "log2" | "linear" | None, "orientation": ... | None,
    "evidence": [...], "reason": str | None}``."""
    rows = _rows(text)
    if not rows:
        return {
            "rows": [],
            "columns": {},
            "headerless": False,
            "candidate_roles": None,
            "scale": None,
            "orientation": None,
            "evidence": [],
            "reason": "the table is empty",
        }
    confirmed = (interpretation or {}).get("columns")
    headerless = _headerless(rows)
    width = max(len(r) for r in rows)
    if headerless and not confirmed:
        return {
            "rows": [],
            "columns": {},
            "headerless": True,
            "width": width,
            "candidate_roles": _candidate_roles(rows),
            "scale": None,
            "orientation": None,
            "evidence": [],
            "reason": "the table is headerless, and no legend, README, methods statement or recorded confirmation "
            "establishes which column is which",
        }
    if confirmed:
        header = [f"column {i + 1}" for i in range(width)] if headerless else rows[0]
        body = rows if headerless else rows[1:]
        index = {role: confirmed.get(role) for role in ("id", "lfc", "pvalue", "padj")}
        beyond = next((role for role, i in index.items() if isinstance(i, int) and i >= width), None)
        if beyond:
            return _rejected(
                headerless,
                f"the recorded interpretation names column {index[beyond] + 1} for the {_ROLE_WORDS[beyond]}, but the "
                f"table has {width} columns, so the interpretation is rejected",
            )
    else:
        header, body = rows[0], rows[1:]
        index = {
            "id": _first(header, _ID_NAMES),
            "lfc": None,
            "pvalue": _first(header, _PVAL_NAMES),
            "padj": _first(header, _PADJ_NAMES),
        }
        lowered = [h.lower() for h in header]
        index["lfc"] = next((i for i, h in enumerate(lowered) if any(w in h for w in _LOG_WORDS)), None)
        if index["lfc"] is None:
            index["lfc"] = next(
                (i for i, h in enumerate(lowered) if any(w in _squash(h) for w in ("foldchange",))), None
            )
        if contrast_name:
            key = contrast_name.strip().lower()
            for i, h in enumerate(lowered):
                if key not in h:
                    continue
                if any(w in h for w in _LOG_WORDS) or "fold" in h:
                    index["lfc"] = i
                elif any(w in _squash(h) for w in ("padj", "fdr", "qvalue", "adj")):
                    index["padj"] = i
                elif any(w in _squash(h) for w in ("pvalue", "pval")):
                    index["pvalue"] = i
        if index["id"] is None:
            index["id"] = 0
        if selector:
            index.update({role: selector.get(role) for role in ("lfc", "pvalue", "padj")})
    columns = {role: (header[i] if isinstance(i, int) and i < len(header) else None) for role, i in index.items()}

    evidence: list[str] = []
    scale = (interpretation or {}).get("effect_scale")
    orientation = (interpretation or {}).get("orientation")
    lfc_name = (columns.get("lfc") or "").lower()
    if scale is None and lfc_name:
        if any(w in lfc_name for w in _LOG_WORDS):
            scale = "log2"
            evidence.append(f"the column's header ({columns['lfc']}) states a log scale")
        elif "fold" in lfc_name:
            scale = "linear"
            evidence.append(f"the column's header ({columns['lfc']}) states a fold change with no log")
    if orientation is None and lfc_name:
        orientation = _header_orientation(columns["lfc"])
        if orientation:
            evidence.append(f"the column's header ({columns['lfc']}) names the ratio's arms in order")

    parsed = []
    for row in body:

        def _cell(role):
            i = index.get(role)
            return row[i] if isinstance(i, int) and i < len(row) else None

        parsed.append(
            {
                "id": _cell("id"),
                "lfc": _number(_cell("lfc") or ""),
                "pvalue": _number(_cell("pvalue") or ""),
                "padj": _number(_cell("padj") or ""),
            }
        )
    if confirmed:
        # plan_8_2 section 3.1: values can reject an interpretation, never establish one.
        for role in ("pvalue", "padj"):
            values = [r[role] for r in parsed if r[role] is not None]
            if values and (min(values) < 0 or max(values) > 1):
                return _rejected(
                    headerless,
                    f"the recorded interpretation reads {columns[role]} as the {_ROLE_WORDS[role]}, but it holds values "
                    "outside 0 to 1, so the interpretation is rejected",
                )
    return {
        "rows": parsed,
        "columns": columns,
        "headerless": headerless,
        "candidate_roles": None,
        "scale": scale,
        "orientation": orientation,
        "evidence": evidence,
        "reason": None,
    }


_ROLE_WORDS = {"id": "identifier", "lfc": "fold change", "pvalue": "P value", "padj": "adjusted P value"}
# plan_8_2 section 3.1: the version of how a table's interpretation is recorded on a comparison.
INTERPRETATION_VERSION = 1


def _rejected(headerless: bool, reason: str) -> dict:
    return {
        "rows": [],
        "columns": {},
        "headerless": headerless,
        "rejected": True,
        "candidate_roles": None,
        "scale": None,
        "orientation": None,
        "evidence": [],
        "reason": reason,
    }


def _needed_columns(predicate: dict) -> list[str]:
    """The columns a check of this predicate reads, in words."""
    needed = ["the identifier"]
    effect = (predicate or {}).get("effect") or {}
    if effect.get("kind") == "abs_log2fc" or (predicate or {}).get("direction") in ("up", "down"):
        needed.append("the fold change")
    kind = ((predicate or {}).get("significance") or {}).get("kind")
    if kind:
        needed.append(f"the {_ROLE_WORDS[kind]}")
    return needed


def _headerless_reason(width: int | None, predicate: dict) -> str:
    """What a headerless table lacks for this check: never a deficiency of the table itself."""
    needed = _needed_columns(predicate)
    words = needed[0] if len(needed) == 1 else f"{', '.join(needed[:-1])} and {needed[-1]}"
    return (
        f"the table is headerless, and nothing bioAF holds (a legend, README, methods statement or recorded "
        f"confirmation) says which of its {width} columns holds {words}; a person who knows can record it"
    )


def _interpretation_view(reading: dict, interpretation: dict | None) -> dict:
    """The interpretation a comparison applied, with what establishes it, recorded beside the result."""
    evidence = list(reading["evidence"])
    if interpretation:
        evidence.insert(
            0,
            f"recorded by {interpretation.get('confirmed_by') or 'a person'}"
            f"{': ' + interpretation['note'] if interpretation.get('note') else ''}",
        )
    return {
        "version": INTERPRETATION_VERSION,
        "source": "confirmation" if interpretation else "header",
        "confirmation_version": (interpretation or {}).get("version"),
        "columns": reading["columns"],
        "effect_scale": reading["scale"],
        "orientation": reading["orientation"],
        "evidence": evidence,
    }


def _header_orientation(name: str) -> dict | None:
    """The arms a ratio's header names, in order: ``log2(KO/WT)`` or ``KO_vs_WT_log2FC``."""
    match = re.search(
        r"([A-Za-z0-9][\w.\- ]*?)\s*(?:/|_vs_|\.vs\.| vs\.? | versus )\s*([A-Za-z0-9][\w.\-]*)", name or ""
    )
    if not match:
        return None
    return {"numerator": match.group(1).strip(" (_"), "denominator": match.group(2).strip(" )_")}


def _orientation_for(orientation, contrast: dict | None) -> str | None:
    """``test_over_reference``, ``reference_over_test`` or None, from what the table's own header (or a
    confirmation) established."""
    if orientation in ("test_over_reference", "reference_over_test"):
        return orientation
    if not isinstance(orientation, dict):
        return None
    test = _squash((contrast or {}).get("test_condition") or "")
    reference = _squash((contrast or {}).get("reference_condition") or "")
    numerator, denominator = _squash(orientation["numerator"]), _squash(orientation["denominator"])
    if test and reference and test in numerator and reference in denominator:
        return "test_over_reference"
    if test and reference and reference in numerator and test in denominator:
        return "reference_over_test"
    return None


def check_claim(
    target: dict,
    predicate: dict,
    table: dict,
    *,
    contrast: dict | None = None,
    interpretation: dict | None = None,
    selector: dict | None = None,
    list_evidence: dict | None = None,
) -> dict:
    """The consistency record for one claim against one table. ``selector`` is the bound contrast's own
    columns in a table that reports several contrasts (plan_8_2 section 1.1). ``list_evidence`` establishes
    the table as the claim's complete selected list, so a count whose cutoff is not stated can be checked as a
    count of that list (plan_8_2 section 3.2)."""
    count = predicate.get("count") or {}
    record = {
        "table": table.get("name"),
        "source": table.get("source"),
        "columns": {},
        "predicate": predicate,
        "assumptions": list(predicate.get("assumptions") or []),
        "counting": {
            "entity": count.get("entity") or "gene",
            "dedup": count.get("dedup") or "distinct_identifier",
            "missing": count.get("missing") or "exclude_and_report",
        },
        "rows_tested": 0,
        "rows_passing": None,
        "rows_missing": 0,
        "count_range": None,
        "duplicates_disagreeing": [],
        "claim": {"value": count.get("value"), "relation": count.get("relation")},
        "candidates": [],
        "candidate_roles": None,
        "outcome": None,
        "reason": None,
    }

    def _done(outcome, reason=None):
        record["outcome"], record["reason"] = outcome, reason
        return record

    if predicate.get("status") == "not_checkable":
        if list_evidence and count and predicate.get("significance") is None:
            # plan_8_3 section 1.2: a claim that REFINES the published list (a fold change on top of
            # the selection the authors already made) is a different operation from counting the list
            # itself, and a verified parent never satisfies the refinement by itself.
            if (predicate.get("effect") or {}).get("kind") in ("abs_log2fc", "log2fc"):
                return _subset_count(record, predicate, table, list_evidence, contrast, _done)
            return _list_count(record, predicate, table, list_evidence, _done)
        return _done(NOT_CHECKABLE, predicate.get("reason"))
    if predicate.get("significance_status") == "unresolved" or predicate.get("status") == "unresolved":
        return _done(UNRESOLVED, predicate.get("reason"))
    if not count:
        return _done(NOT_CHECKABLE, "the claim states no count to check against the table")

    reading = read_table(
        table.get("text") or "",
        contrast_name=(contrast or {}).get("name"),
        interpretation=interpretation,
        selector=selector,
    )
    record["columns"] = reading["columns"]
    if selector:
        record["assumptions"].append(
            "the table reports several contrasts; the columns naming this claim's contrast were read"
        )
    if reading["reason"]:
        record["candidate_roles"] = reading["candidate_roles"]
        if reading.get("rejected"):
            return _done(UNRESOLVED, reading["reason"])
        if reading["headerless"]:
            record["columns_count"] = reading.get("width")
            return _done(UNRESOLVED, _headerless_reason(reading.get("width"), predicate))
        return _done(NOT_CHECKABLE, reading["reason"])
    record["interpretation"] = _interpretation_view(reading, interpretation)
    if interpretation:
        record["assumptions"].append(
            f"the table's interpretation was confirmed by {interpretation.get('confirmed_by') or 'a person'}"
            f"{' on ' + str(interpretation.get('at')) if interpretation.get('at') else ''}"
        )
    record["assumptions"].extend(reading["evidence"])

    significance = predicate.get("significance") or {}
    if significance.get("kind") == "pvalue" and not reading["columns"].get("pvalue"):
        return _done(
            NOT_CHECKABLE, "the table has no P-value column, and bioAF does not substitute the adjusted P value"
        )
    if significance.get("kind") == "padj" and not reading["columns"].get("padj"):
        return _done(
            NOT_CHECKABLE, "the table has no adjusted P-value column, and bioAF does not substitute the raw P value"
        )

    rows = reading["rows"]
    lfcs = [r["lfc"] for r in rows if r["lfc"] is not None]
    effect = predicate.get("effect") or {"kind": "none"}
    direction = predicate.get("direction")
    needs_scale = effect.get("kind") == "abs_log2fc"
    needs_orientation = direction in ("up", "down")
    if (needs_scale or needs_orientation) and not reading["columns"].get("lfc"):
        return _done(NOT_CHECKABLE, "the table has no fold-change column")
    scale = reading["scale"]
    if scale == "linear" and any(v < 0 for v in lfcs):
        scale = None  # ruled out: a linear fold change is never negative
        record["assumptions"].append("the column has negative values, so it cannot be a linear fold change")
    if needs_scale and scale != "log2":
        return _done(
            UNRESOLVED,
            "the table's effect scale is not established by its header, a legend, the methods or a confirmation",
        )

    orientation = _orientation_for(reading["orientation"], contrast)
    record["rows_tested"] = len(rows)

    def _count(pred):
        return count_passing(rows, pred)

    if needs_orientation and orientation is None:
        flipped = {"up": "down", "down": "up"}[direction]
        as_stated, reversed_ = _count(predicate), _count({**predicate, "direction": flipped})
        test, reference = (
            (contrast or {}).get("test_condition") or "test",
            (contrast or {}).get("reference_condition") or "reference",
        )
        record["candidates"] = [
            {"interpretation": f"the table is {test} over {reference}", "count": as_stated["count"]},
            {"interpretation": f"the table is {reference} over {test}", "count": reversed_["count"]},
        ]
        record["rows_missing"] = as_stated["rows_missing"]
        return _done(
            UNRESOLVED,
            "the table's ratio orientation is not established by its header, a legend, the methods or a confirmation, "
            "and the contrast's arm names describe the claim, not the table",
        )
    applied = (
        predicate
        if orientation != "reference_over_test"
        else {**predicate, "direction": {"up": "down", "down": "up"}.get(direction, direction)}
    )
    result = _count(applied)
    record.update(
        rows_passing=result["count"],
        rows_missing=result["rows_missing"],
        count_range=result["count_range"],
        duplicates_disagreeing=result["duplicates_disagreeing"],
    )
    status, words = evaluate_count(count, result["count_range"])
    if status == HOLDS:
        return _done(AGREE, words)
    if status == FAILS:
        return _done(DISAGREE, words)
    return _done(UNRESOLVED, words)


# plan_8_2 section 3.2, labels pending the owner's sign-off.
LIST_COUNT = "published_list_count"
LIST_COUNT_NOTE = (
    "this checks the count of the published list; it does not check the statistical procedure that selected it"
)
_CHROMOSOME_NAMES = ("chr", "chrom", "chromosome", "chromosome_name", "seqnames", "seqname")
_SUBGROUPS = (
    (re.compile(r"sex[- ](?:chromosome[- ])?linked", re.I), {"X", "Y"}, "located on chromosome X or Y"),
    (re.compile(r"\bX[- ]linked", re.I), {"X"}, "located on chromosome X"),
    (re.compile(r"\bY[- ]linked", re.I), {"Y"}, "located on chromosome Y"),
)


def list_subgroup(stated_as: str | None, value) -> dict | None:
    """The chromosome subgroup a count is of, when the paper attaches one to this very number
    ("146 are sex-linked"), with its definition. None for a count of the whole list."""
    text = str(stated_as or "")
    if value is None:
        return None
    for pattern, chromosomes, definition in _SUBGROUPS:
        for match in pattern.finditer(text):
            before = text[max(0, match.start() - 50) : match.start()]
            numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", before)
            if numbers and float(numbers[-1].replace(",", "")) == float(value):
                return {"chromosomes": sorted(chromosomes), "definition": definition}
    return None


def _chromosome(value: str) -> str:
    text = (value or "").strip().strip('"')
    return text[3:].upper() if text.lower().startswith("chr") else text.upper()


def _list_count(record: dict, predicate: dict, table: dict, evidence: dict, done) -> dict:
    """plan_8_2 section 3.2: the claim's count checked as a count of the published list the evidence names.
    Distinct identifiers; rows with no identifier excluded and reported; a subgroup counted from the table's
    own field that defines it. Never a check of the procedure that selected the list."""
    record["method"] = LIST_COUNT
    record["list"] = {
        "evidence": {"text": evidence.get("text"), "source": evidence.get("source")},
        "dedup": "distinct identifier",
        "missing": "rows with no identifier are excluded and reported",
        "subgroup": None,
    }
    record["assumptions"].append(LIST_COUNT_NOTE)
    if evidence.get("partial"):
        return done(
            UNRESOLVED,
            f"the passage calls the file part of the list ({evidence['partial']}), so it is not the claim's complete "
            "selected list",
        )
    rows = _rows(table.get("text") or "")
    if not rows or _headerless(rows):
        return done(UNRESOLVED, "the list is headerless, so which column identifies each entry is not established")
    header, body = rows[0], rows[1:]
    id_index = _first(header, _ID_NAMES)
    if id_index is None:
        id_index = 0
    record["columns"] = {"id": header[id_index] if id_index < len(header) else None}
    for role, names in (("padj", _PADJ_NAMES), ("pvalue", _PVAL_NAMES)):
        column = _first(header, names)
        values = [_number(r[column]) for r in body if column is not None and column < len(r)]
        present = [v for v in values if v is not None]
        if present and max(present) >= 0.5:
            record["columns"][role] = header[column]
            return done(
                UNRESOLVED,
                f"the table holds rows with {header[column]} up to {max(present):g}, values no selected list includes, "
                "so it is not the claim's selected list",
            )
    count = predicate.get("count") or {}
    subgroup = list_subgroup(predicate.get("stated_as"), count.get("value"))
    selected = body
    if subgroup:
        column = _first(header, _CHROMOSOME_NAMES)
        if column is None:
            record["list"]["subgroup"] = {**subgroup, "field": None, "unmapped": None}
            return done(
                UNRESOLVED,
                f"the table has no chromosome field, and bioAF holds no annotation mapping for its identifiers, so the "
                f"subgroup ({subgroup['definition']}) cannot be counted",
            )
        unmapped = sum(1 for r in body if column >= len(r) or not r[column].strip())
        selected = [r for r in body if column < len(r) and _chromosome(r[column]) in subgroup["chromosomes"]]
        record["list"]["subgroup"] = {**subgroup, "field": header[column], "unmapped": unmapped}
        record["columns"]["subgroup"] = header[column]
    identifiers = [r[id_index].strip() for r in selected if id_index < len(r)]
    missing = sum(1 for i in identifiers if not i) + sum(1 for r in selected if id_index >= len(r))
    distinct = {i for i in identifiers if i}
    record.update(
        rows_tested=len(body),
        rows_passing=len(distinct),
        rows_missing=missing,
        count_range=[len(distinct), len(distinct)],
    )
    status, words = evaluate_count(count, [len(distinct), len(distinct)])
    if status == HOLDS:
        return done(AGREE, words)
    if status == FAILS:
        return done(DISAGREE, words)
    return done(UNRESOLVED, words)


def _subset_count(record: dict, predicate: dict, table: dict, evidence: dict, contrast, done) -> dict:
    """plan_8_3 section 1.2: the claim's count checked as a documented refinement of the published list.

    The parent has to be established as the claim's complete published selected set first; the
    refinement's semantics come from the claim's own words and a recorded clarification, never from
    which reading happens to produce the paper's number.
    """
    from app.services.validation_published_subset import SUBSET_NOTE, subset_count

    count = predicate.get("count") or {}
    # plan_8_3 section 1.2: where the refinement was linked to a list the passage CITED, the counts that
    # sentence stated travel with it, and the parent is the one that matches this table's rows. A passage
    # naming a list of another size did not name this table.
    refinement = evidence.get("refinement") or {}
    rows = sum(1 for line in (table.get("text") or "").splitlines() if line.strip()) - 1
    stated_counts = [c for c in refinement.get("parent_counts") or [] if isinstance(c, (int, float))]
    parent_count = next((int(c) for c in stated_counts if int(c) == rows), None)
    parent = {
        "verified": not evidence.get("partial") and (parent_count is not None or not stated_counts),
        "reason": (
            f"the passage calls the file part of the list ({evidence['partial']})"
            if evidence.get("partial")
            else (
                f"the passage states a list of {' or '.join(f'{int(c)}' for c in stated_counts)} rows and this table "
                f"holds {max(rows, 0)}, so it is not the same list"
                if stated_counts and parent_count is None
                else None
            )
        ),
        "source": table.get("name"),
        "checksum": table.get("checksum"),
        "count": parent_count,
    }
    found = subset_count(
        table.get("text") or "",
        parent=parent,
        statement=predicate.get("stated_as") or (evidence.get("text") or ""),
        claimed=count.get("value"),
        confirmation=(table.get("confirmation") or {}).get("filter_semantics"),
    )
    record["method"] = found["method"]
    record["subset"] = {k: v for k, v in found.items() if k not in ("status", "reason")}
    record["list"] = {
        "evidence": {"text": evidence.get("text"), "source": evidence.get("source")},
        "dedup": "distinct identifier",
        "missing": "rows with no identifier are excluded and reported",
        "subgroup": None,
    }
    record["assumptions"].append(SUBSET_NOTE)
    record["rows_tested"] = found.get("rows_tested") or 0
    record["rows_passing"] = found.get("count")
    record["rows_missing"] = (found.get("rows_without_identifier") or 0) + (found.get("rows_without_value") or 0)
    if found.get("count") is not None:
        record["count_range"] = [found["count"], found["count"]]
    if found["status"] == "agree":
        return done(AGREE, found["reason"])
    if found["status"] == "disagree":
        return done(DISAGREE, found["reason"])
    return done(UNRESOLVED, found["reason"])


def supplement_consistency(
    blob: bytes, filename: str, predicates: list[dict], *, table: dict | None = None
) -> list[dict]:
    """Each claim checked against one results supplement while its bytes are in hand. The rows are
    never kept: the record holds counts, the columns used and the outcome. Never raises.

    plan_8_2 section 1.2: decoded by the shared decoder. A table that arrived and could not be
    interpreted gives each claim an unresolved record saying so, with the decoding's provenance.

    plan_8_2 section 1.1: a claim is compared only when the table is bound to its contrast
    (``validation_table_binding``). ``table`` is the supplement as a binding candidate: its labels and the
    passages that cite it. Every record carries its binding; a table not bound to a claim is not compared
    with it, and the record says why."""
    from app.services import validation_table_binding as binding
    from app.services.table_decoding import decode_table

    decoded = decode_table(blob if isinstance(blob, (bytes, bytearray)) else b"", filename)
    if not decoded.ok:
        return [
            {
                "table": filename,
                "source": "supplement",
                "outcome": UNRESOLVED,
                "reason": decoded.reason,
                "decoding": decoded.provenance(),
                "claim_index": item.get("claim_index"),
                "consistency_version": CONSISTENCY_VERSION,
            }
            for item in predicates or []
        ]
    text = decoded.text
    candidate = {"name": filename, "source": "supplement", **(table or {}), "checksum": decoded.source_checksum}
    header = binding.header_of(text)
    records = []
    for item in predicates or []:
        bound = binding.bind(
            candidate, item.get("contrast") or {}, competitors=item.get("competitors") or [], header=header
        )
        if not binding.established(bound):
            records.append(
                {
                    **unbound_record(filename, "supplement", bound),
                    "decoding": decoded.provenance(),
                    "claim_index": item.get("claim_index"),
                    # Every record names the reading that made it, so a recovery that has already read
                    # the bundle under the current one is not offered again for ever.
                    "consistency_version": CONSISTENCY_VERSION,
                }
            )
            continue
        try:
            record = check_claim(
                {},
                item["predicate"],
                # plan_8_3 section 1.2: the confirmation travels on the table, so an operation that
                # reads it reaches it. None here: this comparison is made while the bytes are in hand,
                # and the queued check recomputes with a confirmation once one is recorded.
                {
                    "name": filename,
                    "text": text,
                    "source": "supplement",
                    "checksum": decoded.source_checksum,
                    "confirmation": item.get("confirmation"),
                },
                contrast=item.get("contrast"),
                interpretation=item.get("interpretation"),
                selector=bound.get("selector"),
                list_evidence=binding.list_evidence(
                    candidate, item["predicate"], confirmation=item.get("confirmation")
                ),
            )
        except Exception:  # noqa: BLE001 - one unreadable table never costs the inventory
            continue
        record.pop("predicate", None)
        records.append(
            {
                **record,
                "binding": bound,
                "decoding": decoded.provenance(),
                "claim_index": item.get("claim_index"),
                # The predicate this comparison applied; a held comparison is reused only for the same one.
                "predicate_fingerprint": _fingerprint(item.get("predicate")),
                # And the reading that made it: a record from an earlier one is not this build's answer.
                "consistency_version": CONSISTENCY_VERSION,
            }
        )
    return records


def _fingerprint(predicate) -> str:
    from app.services.validation_check_queue import fingerprint
    from app.services.validation_predicate import predicate_identity

    return fingerprint(predicate_identity(predicate))


def unbound_record(table: str | None, source: str | None, bound: dict) -> dict:
    """plan_8_2 section 1.1: the record for a claim a table is not bound to. Nothing was compared."""
    from app.services import validation_table_binding as binding

    return {
        "table": table,
        "source": source,
        "columns": {},
        "rows_tested": 0,
        "rows_passing": None,
        "rows_missing": 0,
        "count_range": None,
        "duplicates_disagreeing": [],
        "candidates": [],
        "assumptions": [],
        "outcome": UNRESOLVED,
        "reason": binding.binding_reason(bound),
        "binding": bound,
    }


def claim_predicates(targets: list, plan, *, evidence: dict | None = None) -> list[dict]:
    """``[{"claim_index", "predicate", "contrast"}]`` for every claim that reports on a contrast.

    plan_8_2 section 3.1: a claim that states no cutoff inherits one only from a methods sentence the study
    recorded (``evidence["methods_cutoffs"]``) that covers every differential test in its experiment."""
    from app.services.validation_methods_cutoffs import inherited_cutoffs
    from app.services.validation_predicate import build_predicate

    design = (getattr(plan, "differential_design_json", None) or {}) if plan is not None else {}
    contrasts = design.get("contrasts") or []
    experiments = [
        e
        for e in (getattr(plan, "reported_experiments_json", None) or [] if plan is not None else [])
        if isinstance(e, dict)
    ]
    recorded = (evidence or {}).get("methods_cutoffs")
    found = []
    for index, target in enumerate(targets):
        claim = (
            target
            if isinstance(target, dict)
            else {
                "claim_text": target.claim_text,
                "claimed_value": target.claimed_value,
                "output_type": target.output_type,
                "direction": target.direction,
                "contrast_index": target.contrast_index,
                "cutoffs": target.cutoffs,
                "threshold": target.threshold,
                "threshold_kind": target.threshold_kind,
                "count_relation": getattr(target, "count_relation", None),
                "tolerance": target.tolerance,
                "significance_unresolved": target.unresolved_reason,
                "reported_experiment_id": getattr(target, "reported_experiment_id", None),
            }
        )
        position = claim.get("contrast_index")
        if not isinstance(position, int) or not 0 <= position < len(contrasts):
            continue
        contrast = contrasts[position]
        inherited = inherited_cutoffs(
            claim.get("reported_experiment_id") or (contrast or {}).get("reported_experiment_id"),
            experiments=experiments,
            contrasts=contrasts,
            recorded=recorded,
        )
        found.append(
            {
                "claim_index": index,
                "predicate": build_predicate(claim, contrast=contrast, design=design, inherited=inherited),
                "contrast": contrast,
                # plan_8_2 section 1.1: every other contrast of the paper, which a table's binding must rule out.
                "contrast_index": position,
                "competitors": [c for i, c in enumerate(contrasts) if i != position and isinstance(c, dict)],
            }
        )
    return found
