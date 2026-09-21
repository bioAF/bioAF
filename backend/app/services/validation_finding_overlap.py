"""One demonstrated failure is one deduction, and a positive cannot stand on evidence a negative contradicts.

The owner, 2026-09-21, on study 65's deployed run:

    "This discrepancy appears under C5.B, M1.B, and M5.B. Multiple deductions require distinct
    demonstrated failures and consequences, not repeated wording about the same mismatch."

    "M3.B awards positive credit for statistical-design appropriateness without resolving this same
    concern. Those judgments need reconciliation."

Obligations are judged ONE PER REQUEST, which is a design invariant of the judgment contract and is
what keeps an assessor from rating the paper. Neither problem can be seen from inside a single
judgment, so this is the pass that reconciles them afterwards.

It reconciles from what a judgment CITED and what scope it named, not from what its prose happened
to say. Two negatives resting on the same evidence about the same scope are one finding; the
obligation the finding is about keeps the deduction and the other records the cross-reference. A
positive resting on evidence a negative has contradicted cannot stand on it either.

**Nothing is discarded.** A demoted judgment keeps what it found under ``withheld``, because the
reader needs to see that bioAF found the same thing twice, not that it found it once.

Pure: it is given judgments and returns judgments. No network, no model, no database.
"""

from __future__ import annotations

import re

from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

OVERLAP_VERSION = 1

# A measurement outranks a model's review, which is plan_8_5's rule for the same obligation and is
# the same rule here for WHICH of two findings keeps the deduction.
_METHOD_RANK = {"measurement": 3, "human_assisted": 2, "model_assisted": 1}

_WORD = re.compile(r"[a-z0-9_.]+")
# Words that appear in every scope sentence and say nothing about which scope it is.
_COMMON = {
    "the",
    "of",
    "a",
    "an",
    "and",
    "in",
    "for",
    "to",
    "as",
    "its",
    "this",
    "that",
    "step",
    "steps",
    "analysis",
    "paper",
    "data",
    "used",
    "using",
    "from",
    "by",
    "with",
    "on",
}


def _citations(judgment: dict) -> set[str]:
    evidence = judgment.get("evidence") if isinstance(judgment.get("evidence"), dict) else {}
    return {str(c) for c in (evidence or {}).get("citations") or []}


def _scope_words(judgment: dict) -> set[str]:
    text = " ".join(str(judgment.get(key) or "") for key in ("finding_scope", "scope")).lower()
    return {word for word in _WORD.findall(text) if word not in _COMMON and len(word) > 2}


def _same_scope(one: dict, other: dict) -> bool:
    """Whether two findings are about the same thing, from the scope each one named.

    Distinct failures may rest on the same passage: study 65's M1.B (single-cell preprocessing) and
    M5.B (the GO enrichment step) both cite the methods and the same script. What tells them apart
    is what each said it was about.
    """
    a, b = _scope_words(one), _scope_words(other)
    if not a or not b:
        # Neither named a scope, so bioAF cannot tell them apart and does not pretend to.
        return not a and not b
    overlap = len(a & b) / min(len(a), len(b))
    return overlap >= 0.6


def _rank(judgment: dict) -> int:
    return _METHOD_RANK.get(str(judgment.get("method") or "model_assisted"), 1)


def _demote(judgment: dict, rationale: str, **extra) -> dict:
    """Keep the finding, stop it deducting. The reader still sees what bioAF found."""
    return {
        "outcome": UNDETERMINED,
        "rationale": rationale,
        "scope": judgment.get("scope"),
        "method": judgment.get("method"),
        "assessor": judgment.get("assessor"),
        "coverage": judgment.get("coverage"),
        "evidence": judgment.get("evidence"),
        "evidence_ids": judgment.get("evidence_ids"),
        "withheld": {k: v for k, v in judgment.items() if k in ("outcome", "rationale", "impact", "finding_scope")},
        "overlap_version": OVERLAP_VERSION,
        **extra,
    }


def reconcile_findings(judgments: dict[str, dict] | None) -> dict[str, dict]:
    """``judgments``, with duplicate negatives and contradicted positives demoted. Never raises."""
    rows = {leaf: j for leaf, j in (judgments or {}).items() if isinstance(j, dict)}
    found = dict(judgments or {})

    negatives = [(leaf, j) for leaf, j in rows.items() if j.get("outcome") == FAILED and _citations(j)]
    # The obligation whose finding it is keeps the deduction: the better-evidenced method first, then
    # the one resting on more evidence, then the leaf's own order so the result is deterministic.
    negatives.sort(key=lambda pair: (-_rank(pair[1]), -len(_citations(pair[1])), pair[0]))

    kept: list[tuple[str, dict]] = []
    for leaf, judgment in negatives:
        citations = _citations(judgment)
        owner = next(
            (other for other, held in kept if citations <= _citations(held) and _same_scope(judgment, held)),
            None,
        )
        if owner is None:
            kept.append((leaf, judgment))
            continue
        found[leaf] = _demote(
            judgment,
            f"bioAF found this under {owner} as well, on the same evidence and about the same thing, and one "
            f"demonstrated failure is one deduction; see {owner} for the finding and what it costs",
            same_finding_as=owner,
        )

    # A positive cannot rest on evidence a standing negative has contradicted. The test is
    # CONTAINMENT, not overlap: found by running this on study 65, where a negative citing seven
    # background passages demoted every positive that happened to cite two of them and the score
    # fell for the wrong reason. Sharing a passage is not resting on the same fact.
    standing = [(leaf, rows[leaf]) for leaf, _ in kept]
    for leaf, judgment in rows.items():
        if judgment.get("outcome") != VERIFIED or not _citations(judgment):
            continue
        citations = _citations(judgment)
        against = next((other for other, negative in standing if citations <= _citations(negative)), None)
        if against is not None:
            # It rests on nothing the negative did not already account for, so it cannot stand on it.
            found[leaf] = _demote(
                judgment,
                f"this rests on the same evidence as {against}, which found a problem in it that this does "
                f"not resolve, so bioAF has not established it either way; see {against}",
                contradicted_by=against,
            )
            continue
        # It rests on evidence of its own as well. The two are in tension and a person reconciles
        # them; bioAF does not silently withdraw a point it established.
        overlapping = next((other for other, negative in standing if citations & _citations(negative)), None)
        if overlapping is not None:
            found[leaf] = {
                **judgment,
                "tension_with": overlapping,
                "tension": (
                    f"{overlapping} found a problem in evidence this also rests on, and this answer does not "
                    f"address it; both are reported and neither is withdrawn"
                ),
            }
    return found
