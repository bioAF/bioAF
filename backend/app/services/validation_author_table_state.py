"""plan_8_3 section 1.1: what bioAF actually established about the authors' published results.

Study 50's capability text said no author result table is published, while the same report's
completion facts recorded three attachments that were never inspected. The two cannot both be true,
and the one that was shown is the stronger claim about the paper.

Five states, because they have five remedies:

- ``not_inspected``: something the paper published is in hand and was never opened. Nothing follows;
- ``retrieval_failed``: bioAF could not fetch it. A limitation of the attempt, not of the paper;
- ``unsupported_format``: it arrived in a form bioAF cannot read. bioAF's limit, stated as one;
- ``no_eligible_table``: everything bioAF DID inspect holds no result table. Scoped to what was read;
- ``unresolved_applicability``: a result table is in hand and which contrast it reports is not
  established, which is a question about the binding, not about whether it exists.

``not_deposited`` is the ONE state that says the authors published nothing of the kind, and it is
reached only when the paper names no supplement at all.
"""

from __future__ import annotations

AVAILABLE = "available"
NOT_INSPECTED = "not_inspected"
RETRIEVAL_FAILED = "retrieval_failed"
UNSUPPORTED_FORMAT = "unsupported_format"
NO_ELIGIBLE_TABLE = "no_eligible_table"
UNRESOLVED_APPLICABILITY = "unresolved_applicability"
NOT_DEPOSITED = "not_deposited"

STATES = (
    AVAILABLE,
    NOT_INSPECTED,
    RETRIEVAL_FAILED,
    UNSUPPORTED_FORMAT,
    NO_ELIGIBLE_TABLE,
    UNRESOLVED_APPLICABILITY,
    NOT_DEPOSITED,
)

RESULTS_TABLE = "results_table"
# The formats a supplement can arrive in that bioAF holds no reader for. It is bioAF's limit, and the
# sentence says so rather than describing the paper.
_UNREADABLE_FORMATS = ("pdf", "image", "video", "binary")


def _name(row: dict) -> str:
    return str(row.get("identity") or row.get("filename") or row.get("label") or "a supplement")


def _inspected(row: dict) -> bool:
    """Whether bioAF opened this file and established what it holds.

    A row that predates the flag is read as inspected when it carries a role, which is what the
    inspection produces; a row with no role and no flag was never opened.
    """
    if "inspected" in row:
        return bool(row["inspected"])
    return bool(row.get("role"))


def _rows(value) -> list:
    """The rows in a value the evidence recorded, whatever shape it took."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, dict)]
    return []


def author_table_state(*, supplements: list[dict] | None, deposits: list[dict] | None = None) -> dict:
    """What is established about an authors' result table for this paper, and why.

    Order matters, and it runs from the weakest claim to the strongest: anything bioAF has not looked
    at holds the answer open, because a definitive absence cannot be established from sources nobody
    read.
    """
    # Evidence is what a real study recorded, not what a caller promised: `deposit_inventory` is a
    # dict keyed by accession on a real study, and a value that is not a list of rows holds no
    # supplement either way.
    rows = [r for r in [*_rows(supplements), *_rows(deposits)] if isinstance(r, dict)]
    if not rows:
        return {
            "status": NOT_DEPOSITED,
            "reason": "the paper names no supplementary file, so bioAF found nothing that could hold a result table",
            "sources": [],
        }

    bound = [
        r for r in rows if r.get("role") == RESULTS_TABLE and (r.get("binding") or {}).get("status") == "established"
    ]
    if bound:
        return {
            "status": AVAILABLE,
            "reason": f"the authors published {_name(bound[0])}, bound to its contrast",
            "sources": [_name(r) for r in bound],
        }

    uninspected = [r for r in rows if r.get("resolved") and not _inspected(r)]
    if uninspected:
        names = ", ".join(_name(r) for r in uninspected[:3])
        return {
            "status": NOT_INSPECTED,
            "reason": (
                f"bioAF has not inspected what {names} holds, so whether the authors published a result table "
                "is not established"
            ),
            "sources": [_name(r) for r in uninspected],
        }

    failed = [
        r for r in rows if not r.get("resolved") and (r.get("retrieval") or {}).get("status") not in (None, "retrieved")
    ]
    if failed:
        names = ", ".join(_name(r) for r in failed[:3])
        return {
            "status": RETRIEVAL_FAILED,
            "reason": f"bioAF could not retrieve {names}, so what it holds is not established",
            "sources": [_name(r) for r in failed],
        }

    unreadable = [r for r in rows if str(r.get("format") or "") in _UNREADABLE_FORMATS]
    if unreadable:
        names = ", ".join(_name(r) for r in unreadable[:3])
        return {
            "status": UNSUPPORTED_FORMAT,
            "reason": f"bioAF has no reader for {names}, so what it holds is not established",
            "sources": [_name(r) for r in unreadable],
        }

    tables = [r for r in rows if r.get("role") == RESULTS_TABLE]
    if tables:
        return {
            "status": UNRESOLVED_APPLICABILITY,
            "reason": (f"the authors published {_name(tables[0])}, and which contrast it reports is not established"),
            "sources": [_name(r) for r in tables],
        }

    inspected = [r for r in rows if _inspected(r)]
    return {
        "status": NO_ELIGIBLE_TABLE,
        "reason": (
            f"bioAF inspected {len(inspected)} of the paper's supplementary files and found no result table among "
            "them; this is what bioAF read, not what the authors published"
        ),
        "sources": [_name(r) for r in inspected],
    }
