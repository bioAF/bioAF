"""Which findings rest on the same evidence, so a person or a model can read them together.

The owner, 2026-09-21, on study 65's deployed run:

    "This discrepancy appears under C5.B, M1.B, and M5.B. Multiple deductions require distinct
    demonstrated failures and consequences, not repeated wording about the same mismatch."

    "M3.B awards positive credit for statistical-design appropriateness without resolving this same
    concern. Those judgments need reconciliation."

Obligations are judged ONE PER REQUEST, which is a design invariant of the judgment contract and is
what keeps an assessor from rating the paper. Neither problem can be seen from inside a single
judgment, so a pass afterwards has to reconcile them.

**This module finds the candidates. It does not decide anything.** plan_8_7, agreed with the owner on
2026-09-27: "Citation overlap, containment and scope-word similarity may find candidates for review.
Remove their authority to demote a supported finding, merge failures or decide contradiction." The
counterexample is "processing is explicitly described" beside "the described statistical design is
inappropriate", on the same passage and the same analysis: both propositions are true, and comparing
the scopes they named converted the positive to undetermined. What the two findings MEAN is read by
`validation_semantic_reconciliation`, with the shared evidence in front of the configured model.

Pure: it is given judgments and returns groups of leaf ids. No network, no model, no database.
"""

from __future__ import annotations

import re

from app.services.validation_rubric_v3 import FAILED, VERIFIED

# 3: plan_8_7. Overlap proposes; it no longer demotes, merges or decides a contradiction, so a
# judgment accepted under 2 was reconciled by a rule that no longer exists and is asked again.
OVERLAP_VERSION = 3

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

# What `judgment_from` fills `scope` with when the answer named none. It is the same sentence on
# every judgment of every paper, so comparing it finds a match between any two answers: an artefact,
# never a scope bioAF read out of an answer.
_UNNAMED_SCOPE = re.compile(r"^\s*\d+\s+supplied passages\s*$", re.I)

# How much scope vocabulary two findings share before they are worth reading together. It is a
# threshold on a CANDIDATE list now, so it is deliberately loose: a group nobody needed to reconcile
# costs one reading and changes nothing, and a pair that never reaches the reader costs the repair.
_SCOPE_OVERLAP = 0.4


def _citations(judgment: dict) -> set[str]:
    evidence = judgment.get("evidence") if isinstance(judgment.get("evidence"), dict) else {}
    return {str(c) for c in (evidence or {}).get("citations") or []}


def _scope_words(judgment: dict) -> set[str]:
    text = " ".join(
        str(judgment.get(key) or "")
        for key in ("finding_scope", "scope")
        if not _UNNAMED_SCOPE.fullmatch(str(judgment.get(key) or ""))
    ).lower()
    return {word for word in _WORD.findall(text) if word not in _COMMON and len(word) > 2}


def related(one: dict, other: dict) -> bool:
    """Whether two findings are worth reading together: shared evidence, or a shared named scope.

    Distinct failures may rest on the same passage: study 65's M1.B (single-cell preprocessing) and
    M5.B (the GO enrichment step) both cite the methods and the same script. That is exactly why this
    answers "worth reading together" and not "the same thing".
    """
    if _citations(one) & _citations(other):
        return True
    a, b = _scope_words(one), _scope_words(other)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= _SCOPE_OVERLAP


def candidate_groups(judgments: dict[str, dict] | None) -> list[list[str]]:
    """The groups of findings that rest on the same evidence, for semantic reconciliation to read.

    A group is a connected component of the "worth reading together" relation over the findings that
    concluded something. An obligation nobody settled has no proposition to reconcile.
    """
    rows = {
        leaf: judgment
        for leaf, judgment in (judgments or {}).items()
        if isinstance(judgment, dict) and judgment.get("outcome") in (VERIFIED, FAILED)
    }
    leaves = sorted(rows)
    parent = {leaf: leaf for leaf in leaves}

    def find(leaf: str) -> str:
        while parent[leaf] != leaf:
            parent[leaf] = parent[parent[leaf]]
            leaf = parent[leaf]
        return leaf

    for i, one in enumerate(leaves):
        for other in leaves[i + 1 :]:
            if related(rows[one], rows[other]):
                parent[find(one)] = find(other)
    groups: dict[str, list[str]] = {}
    for leaf in leaves:
        groups.setdefault(find(leaf), []).append(leaf)
    return [sorted(group) for group in groups.values() if len(group) > 1]


def reconcile_findings(judgments: dict[str, dict] | None) -> dict[str, dict]:
    """``judgments``, with the groups worth reading together recorded on them and nothing changed.

    plan_8_7 removed this pass's authority to decide. It stays as the deterministic half: every
    finding carries the other findings that rest on its evidence, so a report and a reader can see
    the grouping even where the semantic pass could not run.
    """
    rows = {leaf: j for leaf, j in (judgments or {}).items() if isinstance(j, dict)}
    found = dict(judgments or {})
    for group in candidate_groups(rows):
        for leaf in group:
            others = [other for other in group if other != leaf]
            found[leaf] = {**rows[leaf], "rests_on_shared_evidence_with": others, "overlap_version": OVERLAP_VERSION}
    return found
