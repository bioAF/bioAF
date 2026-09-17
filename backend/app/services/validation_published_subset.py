"""plan_8_3 section 1.2: count a documented refinement of a published parent list.

A complete published list can establish its total while a required refinement of it stays unassessed.
Groff's Supplemental File 3 holds 194 rows, bioAF already agrees with the paper's 194, and the
paper's other claim, that 88 of them clear a fold change of 2, had no operation behind it at all.

The operation is explicit: a verified parent selected set, a source-backed filter, the resulting
subset, and a count of distinct entities. Every part is recorded, and the two counts are separate
checks: a verified parent never satisfies the refinement by itself.

**The filter's semantics come from evidence, never from arithmetic.** The paper's literal wording,
"log2 fold change > 2", is SIGNED. An absolute-value filter over the same list happens to produce the
paper's own 88, and that is a plausible interpretation, not proof that it is the intended operation:
the same table yields both numbers and only evidence picks one. Where the wording does not resolve
magnitude from direction, the check is unresolved with that reason, and a recorded source-backed
clarification (a figure legend, the authors' code, a person's reading of either) is what settles it.
The interpretation is fixed BEFORE anything is counted, so a count can never choose itself.
"""

from __future__ import annotations

import re

SUBSET_VERSION = 1
SUBSET_COUNT = "published_subset_count"

AGREE = "agree"
DISAGREE = "disagree"
UNRESOLVED = "unresolved"

SUBSET_NOTE = (
    "this checks a documented refinement of the published list; it does not check the statistical procedure "
    "that selected either"
)

_ID_NAMES = ("gene", "gene_id", "geneid", "gene_name", "symbol", "id", "feature", "name", "ensembl")
_LFC_NAMES = ("log2foldchange", "log2fc", "logfc", "log2 fold change", "log2fold", "fold_change", "foldchange")
_PADJ_NAMES = ("padj", "adj.p.val", "adjpval", "fdr", "qvalue", "q_value", "adjusted p", "adj_p")
_PVAL_NAMES = ("pvalue", "p_value", "p.value", "pval", "p val")

# The words that say a cutoff is on MAGNITUDE, and the words that say it is on one direction.
_MAGNITUDE_WORDS = re.compile(
    r"absolute|\|log2|\bmagnitude\b|either direction|up or down|up- ?or down|up and down|in both directions", re.I
)
_UP_WORDS = re.compile(r"\bup[- ]?regulated\b|\bupregulated\b|\binduced\b|\bincreased\b|\bhigher in\b", re.I)
_DOWN_WORDS = re.compile(r"\bdown[- ]?regulated\b|\bdownregulated\b|\brepressed\b|\bdecreased\b|\blower in\b", re.I)

_LOG2 = re.compile(r"log2|log-2|log\s*2", re.I)
_INCLUSIVE = re.compile(r"at least|no less than|or (?:more|greater|higher)|\bof\b\s*$", re.I)
_CUTOFF = re.compile(
    r"(?:log2\s*)?fold[- ]change[^.;]{0,40}?(>=|<=|>|<|greater than or equal to|greater than|at least|of|above|over)"
    r"\s*(-?\d+(?:\.\d+)?)"
    # "more than 2-fold", "> 2-fold": the number leads the word rather than following it.
    r"|(>=|<=|>|<|more than|greater than|at least|over|above)\s*(-?\d+(?:\.\d+)?)[- ]?fold",
    re.I,
)
_PVALUE_CUTOFF = re.compile(r"(adjusted\s*p|padj|fdr|q[- ]?value|\bp\b)[^.;]{0,20}?(<=|<)\s*(-?\d*\.?\d+)", re.I)

_MISSING = {"", "na", "n/a", "nan", "null", "none", "."}


def _number(value: str) -> float | None:
    text = str(value or "").strip().strip('"')
    if text.lower() in _MISSING:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    squashed = [re.sub(r"[^a-z0-9]", "", c.lower()) for c in header]
    wanted = [re.sub(r"[^a-z0-9]", "", n.lower()) for n in names]
    for i, cell in enumerate(squashed):
        if cell in wanted:
            return i
    for i, cell in enumerate(squashed):
        if any(w and w in cell for w in wanted):
            return i
    return None


def filter_from_statement(statement: str | None) -> dict | None:
    """The refinement a passage states, with what it leaves unresolved. None when it states no cutoff.

    ``magnitude`` is True for an absolute cutoff, False for a signed one, and None when the wording
    does not say, which is the case the paper's own words most often leave.
    """
    text = str(statement or "")
    significance = _PVALUE_CUTOFF.search(text)
    match = _CUTOFF.search(text)
    if match is None and significance is None:
        return None
    if match is None:
        kind = "padj" if re.match(r"adjusted|padj|fdr|q", significance.group(1), re.I) else "pvalue"
        return {
            "kind": kind,
            "operator": significance.group(2),
            "value": float(significance.group(3)),
            "scale": None,
            "magnitude": None,
            "direction": None,
            "unresolved": None,
            "statement": text,
            "resolved_by": "statement",
        }
    operator = (match.group(1) or match.group(3) or ">").strip().lower()
    value = float(match.group(2) or match.group(4))
    operator = {
        "more than": ">",
        "greater than": ">",
        "above": ">",
        "over": ">",
        "greater than or equal to": ">=",
        "at least": ">=",
        "of": ">=",
    }.get(operator, operator)
    magnitude = None
    direction = None
    if _MAGNITUDE_WORDS.search(text):
        magnitude = True
    elif _UP_WORDS.search(text) and not _DOWN_WORDS.search(text):
        magnitude, direction = False, "up"
    elif _DOWN_WORDS.search(text) and not _UP_WORDS.search(text):
        magnitude, direction = False, "down"
    return {
        "kind": "abs_log2fc" if magnitude else "log2fc",
        "operator": operator,
        "value": value,
        "scale": "log2" if _LOG2.search(text) else None,
        "magnitude": magnitude,
        "direction": direction,
        "unresolved": (
            None
            if magnitude is not None
            else "the statement does not say whether the cutoff is on the magnitude of the fold change or on its "
            "signed value, and the two select different genes"
        ),
        "statement": text,
        "resolved_by": "statement",
    }


def _done(status: str, reason: str, record: dict) -> dict:
    return {**record, "status": status, "reason": reason}


def subset_count(
    table_text: str,
    *,
    parent: dict,
    statement: str | None,
    claimed,
    confirmation: dict | None = None,
) -> dict:
    """The claim's count checked as a documented refinement of the verified parent list. Never raises.

    ``parent`` is the parent list as the consistency check established it: verified as the claim's
    complete published selected set, with its source and its row count. ``confirmation`` is a recorded
    source-backed clarification, which may resolve the filter's semantics and nothing else.
    """
    record = {
        "method": SUBSET_COUNT,
        "version": SUBSET_VERSION,
        "parent": {
            "source": parent.get("source"),
            "checksum": parent.get("checksum"),
            "count": parent.get("count"),
            "verified": bool(parent.get("verified")),
        },
        "filter": None,
        "count": None,
        "claimed": claimed,
        "rows_tested": None,
        "rows_without_identifier": 0,
        "rows_without_value": 0,
        "duplicates": 0,
        "note": SUBSET_NOTE,
    }
    if not parent.get("verified"):
        return _done(
            UNRESOLVED,
            "the parent is not established as the claim's complete published selected list"
            + (f": {parent['reason']}" if parent.get("reason") else ""),
            record,
        )

    rows = [ln.split("\t") if "\t" in ln else ln.split(",") for ln in (table_text or "").splitlines() if ln.strip()]
    if len(rows) < 2:
        return _done(UNRESOLVED, "the parent list holds no rows to refine", record)
    header, body = [c.strip().strip('"') for c in rows[0]], rows[1:]
    record["rows_tested"] = len(body)
    if isinstance(parent.get("count"), int) and parent["count"] != len(body):
        return _done(
            UNRESOLVED,
            f"the parent list was established with {parent['count']} rows and this table holds {len(body)}, so it is "
            "not the same list",
            record,
        )

    found = filter_from_statement(statement)
    if found is None:
        return _done(UNRESOLVED, "the claim states no refinement of the published list that bioAF can apply", record)
    if found.get("magnitude") is None and found.get("kind") in ("abs_log2fc", "log2fc"):
        # A confirmation resolves the semantics, and only where it actually speaks to them.
        resolved = (confirmation or {}).get("magnitude")
        if isinstance(resolved, bool):
            found = {
                **found,
                "magnitude": resolved,
                "kind": "abs_log2fc" if resolved else "log2fc",
                "unresolved": None,
                "resolved_by": "confirmation",
                "note": (confirmation or {}).get("note"),
                "confirmed_by": (confirmation or {}).get("confirmed_by"),
            }
    record["filter"] = found
    if found.get("unresolved"):
        return _done(UNRESOLVED, found["unresolved"], record)

    id_index = _column(header, _ID_NAMES)
    if id_index is None:
        id_index = 0
    if found["kind"] in ("abs_log2fc", "log2fc"):
        value_index = _column(header, _LFC_NAMES)
        if value_index is None:
            return _done(
                UNRESOLVED, "the parent list has no fold-change column, so the refinement cannot be applied", record
            )
    else:
        names = _PADJ_NAMES if found["kind"] == "padj" else _PVAL_NAMES
        value_index = _column(header, names)
        if value_index is None:
            words = "adjusted P value" if found["kind"] == "padj" else "P value"
            return _done(
                UNRESOLVED,
                f"the parent list has no {words} column, so the refinement cannot be applied",
                record,
            )

    passes = _comparison(found["operator"])
    identifiers: list[str] = []
    for row in body:
        raw = row[value_index].strip() if value_index < len(row) else ""
        value = _number(raw)
        if value is None:
            record["rows_without_value"] += 1
            continue
        measured = abs(value) if found.get("magnitude") else value
        if found.get("direction") == "down":
            measured = -value
        if not passes(measured, found["value"]):
            continue
        identifier = row[id_index].strip().strip('"') if id_index < len(row) else ""
        if not identifier:
            record["rows_without_identifier"] += 1
            continue
        identifiers.append(identifier)
    distinct = set(identifiers)
    record["count"] = len(distinct)
    record["duplicates"] = len(identifiers) - len(distinct)
    record["columns"] = {"id": header[id_index], "value": header[value_index]}
    if not isinstance(claimed, (int, float)) or isinstance(claimed, bool):
        return _done(UNRESOLVED, "the claim states no number to compare the refinement's count with", record)
    if float(claimed) == float(len(distinct)):
        return _done(AGREE, f"{len(distinct)} distinct identifiers, as the paper states", record)
    return _done(
        DISAGREE,
        f"{len(distinct)} distinct identifiers, and the paper states {claimed:g}".rstrip("0").rstrip("."),
        record,
    )


def _comparison(operator: str):
    return {
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
    }.get(operator, lambda a, b: a > b)
