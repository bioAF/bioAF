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
    return [[c.strip().strip('"') for c in row] for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)]


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


def read_table(text: str, *, contrast_name: str | None = None, interpretation: dict | None = None) -> dict:
    """The table's rows keyed by role, the columns used, and what its header establishes.

    Returns ``{"rows": [{"id", "lfc", "pvalue", "padj"}], "columns": {...}, "headerless": bool,
    "candidate_roles": {...} | None, "scale": "log2" | "linear" | None, "orientation": ... | None,
    "evidence": [...], "reason": str | None}``."""
    rows = _rows(text)
    if not rows:
        return {"rows": [], "columns": {}, "headerless": False, "candidate_roles": None, "scale": None,
                "orientation": None, "evidence": [], "reason": "the table is empty"}
    confirmed = (interpretation or {}).get("columns")
    headerless = _headerless(rows)
    if headerless and not confirmed:
        return {"rows": [], "columns": {}, "headerless": True, "candidate_roles": _candidate_roles(rows), "scale": None,
                "orientation": None, "evidence": [],
                "reason": "the table is headerless, and no legend, README, methods statement or recorded confirmation "
                "establishes which column is which"}
    if confirmed:
        header = [f"column {i}" for i in range(max(len(r) for r in rows))] if headerless else rows[0]
        body = rows if headerless else rows[1:]
        index = {role: confirmed.get(role) for role in ("id", "lfc", "pvalue", "padj")}
    else:
        header, body = rows[0], rows[1:]
        index = {"id": _first(header, _ID_NAMES), "lfc": None, "pvalue": _first(header, _PVAL_NAMES),
                 "padj": _first(header, _PADJ_NAMES)}
        lowered = [h.lower() for h in header]
        index["lfc"] = next((i for i, h in enumerate(lowered) if any(w in h for w in _LOG_WORDS)), None)
        if index["lfc"] is None:
            index["lfc"] = next((i for i, h in enumerate(lowered) if any(w in _squash(h) for w in ("foldchange",))), None)
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
    return {"rows": parsed, "columns": columns, "headerless": headerless, "candidate_roles": None, "scale": scale,
            "orientation": orientation, "evidence": evidence, "reason": None}


def _header_orientation(name: str) -> dict | None:
    """The arms a ratio's header names, in order: ``log2(KO/WT)`` or ``KO_vs_WT_log2FC``."""
    match = re.search(r"([A-Za-z0-9][\w.\- ]*?)\s*(?:/|_vs_|\.vs\.| vs\.? | versus )\s*([A-Za-z0-9][\w.\-]*)", name or "")
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
) -> dict:
    """The consistency record for one claim against one table."""
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
        return _done(NOT_CHECKABLE, predicate.get("reason"))
    if predicate.get("significance_status") == "unresolved" or predicate.get("status") == "unresolved":
        return _done(UNRESOLVED, predicate.get("reason"))
    if not count:
        return _done(NOT_CHECKABLE, "the claim states no count to check against the table")

    reading = read_table(table.get("text") or "", contrast_name=(contrast or {}).get("name"), interpretation=interpretation)
    record["columns"] = reading["columns"]
    if reading["reason"]:
        record["candidate_roles"] = reading["candidate_roles"]
        return _done(UNRESOLVED if reading["headerless"] else NOT_CHECKABLE, reading["reason"])
    if interpretation:
        record["assumptions"].append(
            f"the table's interpretation was confirmed by {interpretation.get('confirmed_by') or 'a person'}"
            f"{' on ' + str(interpretation.get('at')) if interpretation.get('at') else ''}"
        )
    record["assumptions"].extend(reading["evidence"])

    significance = predicate.get("significance") or {}
    if significance.get("kind") == "pvalue" and not reading["columns"].get("pvalue"):
        return _done(NOT_CHECKABLE, "the table has no P-value column, and bioAF does not substitute the adjusted P value")
    if significance.get("kind") == "padj" and not reading["columns"].get("padj"):
        return _done(NOT_CHECKABLE, "the table has no adjusted P-value column, and bioAF does not substitute the raw P value")

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
        return _done(UNRESOLVED, "the table's effect scale is not established by its header, a legend, the methods or a confirmation")

    orientation = _orientation_for(reading["orientation"], contrast)
    record["rows_tested"] = len(rows)

    def _count(pred):
        return count_passing(rows, pred)

    if needs_orientation and orientation is None:
        flipped = {"up": "down", "down": "up"}[direction]
        as_stated, reversed_ = _count(predicate), _count({**predicate, "direction": flipped})
        test, reference = (contrast or {}).get("test_condition") or "test", (contrast or {}).get("reference_condition") or "reference"
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
    applied = predicate if orientation != "reference_over_test" else {
        **predicate, "direction": {"up": "down", "down": "up"}.get(direction, direction)
    }
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


def supplement_consistency(blob: bytes, filename: str, predicates: list[dict]) -> list[dict]:
    """Each claim checked against one results supplement while its bytes are in hand. The rows are
    never kept: the record holds counts, the columns used and the outcome. Never raises."""
    try:
        text = blob.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return []
    records = []
    for item in predicates or []:
        try:
            record = check_claim(
                {},
                item["predicate"],
                {"name": filename, "text": text, "source": "supplement"},
                contrast=item.get("contrast"),
                interpretation=item.get("interpretation"),
            )
        except Exception:  # noqa: BLE001 - one unreadable table never costs the inventory
            continue
        record.pop("predicate", None)
        records.append({**record, "claim_index": item.get("claim_index")})
    return records


def claim_predicates(targets: list, plan) -> list[dict]:
    """``[{"claim_index", "predicate", "contrast"}]`` for every claim that reports on a contrast."""
    from app.services.validation_predicate import build_predicate

    design = (getattr(plan, "differential_design_json", None) or {}) if plan is not None else {}
    contrasts = design.get("contrasts") or []
    found = []
    for index, target in enumerate(targets):
        claim = target if isinstance(target, dict) else {
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
        }
        position = claim.get("contrast_index")
        if not isinstance(position, int) or not 0 <= position < len(contrasts):
            continue
        contrast = contrasts[position]
        found.append({"claim_index": index, "predicate": build_predicate(claim, contrast=contrast, design=design), "contrast": contrast})
    return found
