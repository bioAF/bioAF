"""plan_8_7 stage 2: one assessment summary and the six areas a scientist actually asked about.

The report opened on two prominent scorecards with different semantics, and then four engineering
sections: Findings, Data and code, Checks performed, Run diagnostics. A scientist deciding whether to
rely on a paper has six questions, and plan_8_7 section 3 names them:

| Data and metadata | what resources exist, their access and retrieval state, the samples reconciled |
| Experimental methods | whether the stated procedure can be followed |
| Computational methods | whether the processing, references and statistical design are specified |
| Published code and environment | what source was inspected, and what was loaded or executed |
| Results and reproduction | author consistency, author-code execution and independent reanalysis |
| Interpretation | whether the result supports the conclusion in the design examined |

Three rules the old shape broke:

- **an area holds supported observations, concerns and untested work together.** "Do not force a whole
  section into one pass/fail label." There is no outcome field on an area, on purpose.
- **the summary leads and carries no number.** The numerical rubric stays available as secondary
  detail with its own counts and scope; it is not the headline and there is not a second one beside it.
- **the counts are explicit and separate.** Obligations attempted, obligations conclusive and findings
  conclusive are three numbers, and a comparison that finished without resolving is not an agreement.

Pure: it is given the projection and returns the projection's own statements, regrouped. No model, no
database, no retrieval. A synthesis a model wrote can only summarise what is here
(`validation_report_synthesis`), and where it fails this is the factual summary it falls back to.
"""

from __future__ import annotations

from app.services.validation_rubric_v3 import FAILED, VERIFIED

# Which criteria answer which of the six questions. Every criterion rubric v3 declares is in exactly
# one area: an obligation that reached no area would be a check nobody could read the result of.
_AREA_CRITERIA: dict[str, tuple[str, ...]] = {
    "data": ("S1", "S2", "S3", "S4", "S5"),
    "experimental_methods": ("E1", "E2", "E3"),
    "computational_methods": ("M1", "M2", "M3", "M4"),
    "code": ("M5", "C1", "C2", "C3", "C4", "C5"),
    "results": ("R1", "R2", "R3"),
    "interpretation": (),
}

AREAS: list[dict] = [
    {
        "key": "data",
        "title": "Data and metadata",
        "question": "What data and sample metadata does this paper have, and do they agree with each other?",
    },
    {
        "key": "experimental_methods",
        "title": "Experimental methods",
        "question": "Could the stated bench procedure be followed as written?",
    },
    {
        "key": "computational_methods",
        "title": "Computational methods",
        "question": "Are the processing, references, statistical design and decision criteria specified and appropriate?",
    },
    {
        "key": "code",
        "title": "Published code and environment",
        "question": "What source did bioAF inspect, and what was actually loaded or run?",
    },
    {
        "key": "results",
        "title": "Results and reproduction",
        "question": "Do the authors' own results hold together, and what did running anything establish?",
    },
    {
        "key": "interpretation",
        "title": "Interpretation",
        "question": "Does the result support the conclusion, in the design and population actually examined?",
    },
]

_AREA_OF_CRITERION = {criterion: key for key, criteria in _AREA_CRITERIA.items() for criterion in criteria}


def _criterion_of(leaf_id: str) -> str:
    """``M3.B#exp:e1`` -> ``M3``. The unit and the obligation are not what decides the area."""
    return str(leaf_id).partition("#")[0].partition(".")[0]


def _statement(leaf_id: str, outcome: dict) -> dict:
    """One obligation's own words, with where it applies and what it costs."""
    return {
        "leaf": leaf_id,
        "statement": str(outcome.get("rationale") or "").strip(),
        "scope": outcome.get("scope"),
        "impact": outcome.get("impact"),
        "method": outcome.get("method"),
        "next_action": outcome.get("next_action"),
        "citations": (outcome.get("evidence") or {}).get("citations")
        if isinstance(outcome.get("evidence"), dict)
        else None,
        "coverage": outcome.get("coverage"),
        # A judgment the semantic pass demoted keeps what it found, and a reader needs to see it.
        "withheld": outcome.get("withheld"),
        "capability_limit": bool(outcome.get("capability_limit")),
    }


def _rows(outcomes: dict, criteria: tuple[str, ...]) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {"supported": [], "concerns": [], "untested": []}
    for leaf_id, outcome in sorted((outcomes or {}).items()):
        if not isinstance(outcome, dict) or _criterion_of(leaf_id) not in criteria:
            continue
        row = _statement(leaf_id, outcome)
        if not row["statement"]:
            continue
        bucket = {VERIFIED: "supported", FAILED: "concerns"}.get(str(outcome.get("outcome")), "untested")
        found[bucket].append(row)
    return found


def _interpretation_rows(review: dict | None) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {"supported": [], "concerns": [], "untested": []}
    for identity, row in sorted(((review or {}).get("reviews") or {}).items()):
        if not isinstance(row, dict):
            continue
        entry = {
            "leaf": identity,
            "statement": str(row.get("rationale") or "").strip(),
            "inferential_step": row.get("inferential_step"),
            "impact": row.get("impact"),
            "method": row.get("method"),
            "next_action": row.get("next_action"),
            "citations": (row.get("evidence") or {}).get("citations")
            if isinstance(row.get("evidence"), dict)
            else None,
            # plan_8_7 section 3: what a reproduction established travels BESIDE the interpretation.
            # Agreement with the authors' output does not establish that their interpretation follows.
            "reproduction": row.get("reproduction"),
        }
        if not entry["statement"]:
            continue
        bucket = {"supported": "supported", "contradicted": "concerns"}.get(str(row.get("outcome")), "untested")
        found[bucket].append(entry)
    return found


def _reproduction(projection: dict) -> dict:
    """What running anything established, as its own statement. Depth is never folded into the score.

    plan_8_7 section 6: "An unresolved comparison that finished running is not an agreement."
    """
    comparisons = projection.get("comparisons") if isinstance(projection.get("comparisons"), dict) else {}
    attempt = projection.get("attempt") if isinstance(projection.get("attempt"), dict) else {}
    measured = [m for m in projection.get("measured") or [] if isinstance(m, dict)]
    agreed = bool(measured) and all(m.get("agrees") is True for m in measured)
    unresolved = any(m.get("agrees") is None for m in measured)
    return {
        "attempted": bool(attempt.get("attempted")),
        "performed": bool((comparisons or {}).get("performed")),
        "label": (comparisons or {}).get("label"),
        "reason": (comparisons or {}).get("reason"),
        "comparisons": measured,
        "agreed": agreed,
        # A comparison that ran and did not resolve is neither an agreement nor a disagreement.
        "unresolved": unresolved,
    }


_EMPTY = {
    "data": "bioAF has not established what data and sample metadata this paper holds.",
    "experimental_methods": "bioAF has not assessed whether this paper's stated procedure could be followed.",
    "computational_methods": "bioAF has not assessed how this paper's data were processed or analysed.",
    "code": "bioAF has not inspected any source code for this paper.",
    "results": "bioAF has not checked this paper's own results and has reproduced nothing.",
    "interpretation": "bioAF has not assessed whether this paper's results support its conclusions.",
}


def _summary_line(key: str, rows: dict[str, list[dict]]) -> str:
    supported, concerns, untested = len(rows["supported"]), len(rows["concerns"]), len(rows["untested"])
    if not (supported or concerns or untested):
        return _EMPTY[key]
    parts = []
    if supported:
        parts.append(f"{supported} supported observation{'' if supported == 1 else 's'}")
    if concerns:
        parts.append(f"{concerns} demonstrated concern{'' if concerns == 1 else 's'}")
    if untested:
        parts.append(f"{untested} question{'' if untested == 1 else 's'} bioAF could not settle")
    return ", ".join(parts) + "."


def areas_for(projection: dict) -> list[dict]:
    """The six areas of plan_8_7 section 3, each with its own supported, concerns and untested rows."""
    outcomes = ((projection.get("assessment") or {}) if isinstance(projection.get("assessment"), dict) else {}).get(
        "outcomes"
    ) or {}
    review = (
        projection.get("interpretation_review") if isinstance(projection.get("interpretation_review"), dict) else None
    )
    found: list[dict] = []
    for area in AREAS:
        rows = (
            _interpretation_rows(review)
            if area["key"] == "interpretation"
            else _rows(outcomes, _AREA_CRITERIA[area["key"]])
        )
        entry = {**area, **rows, "summary": _summary_line(area["key"], rows)}
        if area["key"] == "results":
            # Reproduction depth is separate from the documentary account of the results (section 6).
            entry["reproduction"] = _reproduction(projection)
        if area["key"] == "data":
            entry["resources"] = [r for r in projection.get("resources") or [] if isinstance(r, dict)]
        if area["key"] == "code":
            entry["sources"] = [s for s in projection.get("code_sources") or [] if isinstance(s, dict)]
        if area["key"] == "interpretation" and review is not None:
            entry["scope"] = review.get("scope")
        found.append(entry)
    return found


def _counts(outcomes: dict, review: dict | None) -> dict:
    """Three numbers a reader can add up, kept apart because they answer different questions.

    The owner's September 21 assessment found claim counts on one report that disagreed with each
    other. An obligation bioAF asked about, an obligation it settled and a demonstrated finding are
    three different quantities, and calling any of them "checks" is how they drifted.
    """
    rows = [row for row in (outcomes or {}).values() if isinstance(row, dict)]
    conclusive = [row for row in rows if row.get("outcome") in (VERIFIED, FAILED)]
    reviews = [row for row in ((review or {}).get("reviews") or {}).values() if isinstance(row, dict)]
    return {
        "obligations_attempted": len(rows),
        "obligations_conclusive": len(conclusive),
        "findings_conclusive": len([row for row in conclusive if row.get("outcome") == FAILED]),
        "obligations_untested": len(rows) - len(conclusive),
        "conclusions_reviewed": len(reviews),
        "conclusions_unresolved": len([row for row in reviews if row.get("outcome") == "unresolved"]),
    }


# How many rows the leading summary carries. It is a synthesis, not the report: the areas hold
# everything and the summary names what a reader has to see before they expand anything.
MAX_SUMMARY_ROWS = 4


def _consequential(rows: list[dict]) -> list[dict]:
    """The rows that carry a stated consequence first: an impact is what makes a concern consequential."""
    return sorted(rows, key=lambda row: (0 if row.get("impact") else 1, str(row.get("leaf"))))[:MAX_SUMMARY_ROWS]


def assessment_summary(projection: dict) -> dict:
    """The factual account that leads the report, and the fallback for a failed model synthesis.

    plan_8_7 section 6: it summarises accepted scoped outcomes and nothing else. It awards no points,
    creates no findings and upgrades no reproduction status, so it carries no score and no headline
    verdict of its own: the numerical rubric is secondary detail beside it.
    """
    held = projection.get("assessment") if isinstance(projection.get("assessment"), dict) else None
    review = (
        projection.get("interpretation_review") if isinstance(projection.get("interpretation_review"), dict) else None
    )
    outcomes = (held or {}).get("outcomes") or {}
    areas = areas_for(projection)
    supported = [row for area in areas for row in area["supported"]]
    concerns = [row for area in areas for row in area["concerns"]]
    untested = [row for area in areas for row in area["untested"]]
    counts = _counts(outcomes, review)
    return {
        "assessment_revision": (held or {}).get("revision"),
        "assessed_at": (held or {}).get("at"),
        "checker_version": (held or {}).get("checker_version"),
        "supported": _consequential(supported),
        "concerns": _consequential(concerns),
        "untested": _consequential(untested),
        "supported_count": len(supported),
        "concern_count": len(concerns),
        "untested_count": len(untested),
        "counts": counts,
        "reproduction": _reproduction(projection),
        "reason": None
        if held
        else "bioAF has not published an assessment of this paper yet, so there is nothing to summarise",
        "method": "factual",
    }
