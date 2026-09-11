"""change_7.2 section 4: reconciliation settles its dependents, or reports them unresolved.

Reaching the reconciliation stage is not the same as the report being consistent. Study 33 shipped a
pre-compute check asserting that per-sample assignments are recoverable BESIDE a plan blocker
asserting they cannot be reconstructed. Both were stated with confidence, in the same report, about
the same paper, and nothing looked at the pair.

They come from two different model calls. `samples_described_enough` is its own decision in
`validation_precompute_checks`; the conflicting blocker comes from the extraction. It is a
cross-step consistency failure, which is why reconciliation is where it has to be resolved.

**A contradiction is resolved by evidence, or reported as unresolved.** Shipping with both standing
is not acceptable. Neither is picking one at random: where the inspected evidence settles which
statement is true, it settles it and says so; where it does not, the report says the two disagree and
that bioAF could not tell which is right.

**No universal consistency service.** This resolves the pairs the assessment actually produces, over
the evidence the assessment actually holds. A general rule engine over free prose would invent
contradictions it cannot resolve.
"""

from __future__ import annotations

import re

from app.services.validation_precompute_checks import CHECK_SAMPLES_DESCRIBED, OK, UNKNOWN

RESOLVED = "resolved"
UNRESOLVED = "unresolved"

# change_7.3 section 7: a blocker carries a kind from the extraction, and this pass reads the kind.
# The two regexes below matched the blocker's prose, so "Sample IDs assigned to each differential
# group ... are not explicitly enumerated in the text" read as no contradiction: in a blob a mistyped
# key reads as no context, and in prose a paraphrase reads as no contradiction. The regexes stay only
# for blockers recorded before kinds existed.
BLOCKER_SAMPLE_ASSIGNMENT = "sample_assignment"
BLOCKER_DATA_ACCESS = "data_access"
BLOCKER_MISSING_DETAIL = "missing_detail"
BLOCKER_NO_ACCESSION = "no_accession"
BLOCKER_METHOD = "method_mismatch"
BLOCKER_OTHER = "other"
BLOCKER_KINDS = (
    BLOCKER_SAMPLE_ASSIGNMENT,
    BLOCKER_DATA_ACCESS,
    BLOCKER_MISSING_DETAIL,
    BLOCKER_NO_ACCESSION,
    BLOCKER_METHOD,
    BLOCKER_OTHER,
)

# change_7.3 section 6: the pairs this pass actually compares. An empty contradiction list means "no
# contradiction among THESE", and the report says which, rather than implying the whole assessment
# was established consistent.
CHECKED_PAIRS = ("samples_described_enough against the plan's blockers about per-sample group assignments",)

# What a blocker says when it asserts the sample-level design cannot be recovered. Matched on the
# blocker's own wording, because a blocker is a sentence the extraction wrote, not a typed field.
_SAMPLE_RECOVERY_BLOCKER = re.compile(
    r"(per[- ]sample|sample[- ]level|sample assignment|which sample|sample identit)"
    r".{0,80}?"
    r"(cannot|could not|not (be )?(recover|reconstruct|determin|establish)|unavailable|missing)",
    re.I | re.S,
)
# And the reverse ordering, which the same claim is written in about as often.
_SAMPLE_RECOVERY_BLOCKER_REVERSED = re.compile(
    r"(cannot|could not|no way to|not possible to)"
    r".{0,80}?"
    r"(recover|reconstruct|determine|establish|assign)"
    r".{0,40}?"
    r"(per[- ]sample|sample[- ]level|sample assignment|which sample)",
    re.I | re.S,
)


# And the wording that says the assignments are not STATED, which is the same claim. Study 34's plan,
# recorded before blockers had kinds, said "Sample IDs assigned to each differential group ... are not
# explicitly enumerated in the text", and its resume on the deployed build found no contradiction.
_SAMPLE_RECOVERY_UNSTATED = re.compile(
    r"(sample ids?|sample identit|sample assignment|per[- ]sample|which sample|assigned to each)"
    r".{0,160}?"
    r"not (explicitly |fully |clearly )?(enumerated|listed|stated|specified|given|reported|provided)",
    re.I | re.S,
)


def _asserts_samples_unrecoverable(blocker: str) -> bool:
    text = str(blocker or "")
    return bool(
        _SAMPLE_RECOVERY_BLOCKER.search(text)
        or _SAMPLE_RECOVERY_BLOCKER_REVERSED.search(text)
        or _SAMPLE_RECOVERY_UNSTATED.search(text)
    )


def _sample_metadata_evidence(supplements: list[dict] | None) -> dict | None:
    """A retrieved, inspected resource that actually carries per-sample rows, or None."""
    for supplement in supplements or []:
        if not isinstance(supplement, dict) or not supplement.get("resolved"):
            continue
        if supplement.get("role") != "sample_metadata":
            continue
        if (supplement.get("row_count") or 0) > 0:
            return supplement
    return None


def blocker_kind(text: str, kinds: dict[str, str]) -> str | None:
    """The kind the extraction gave this blocker, or None for a blocker recorded before kinds."""
    return kinds.get(str(text))


def _kinds_by_text(blocker_kinds: list[dict] | None) -> dict[str, str]:
    return {
        str(k.get("text")): str(k.get("kind"))
        for k in blocker_kinds or []
        if isinstance(k, dict) and k.get("text") and k.get("kind") in BLOCKER_KINDS
    }


def _metadata_candidates(supplements: list[dict] | None) -> list[str]:
    """Attachments not retrieved whose name suggests they list samples. A hedge about where the
    answer may be, never a role: a role comes only from inspected content."""
    hints = ("metadata", "sample", "clinical", "phenotype", "design", "annotation", "cohort", "donor", "patient")
    names = []
    for row in supplements or []:
        if not isinstance(row, dict) or row.get("resolved") or row.get("kind") in ("index", "figure"):
            continue
        text = f"{row.get('filename') or ''} {' '.join(row.get('references') or [])}".lower()
        if any(h in text for h in hints):
            names.append(str(row.get("label") or row.get("filename")))
    return names


def reconcile_contradictions(
    *,
    precompute_checks: dict | None,
    blockers: list[str] | None,
    supplements: list[dict] | None,
    blocker_kinds: list[dict] | None = None,
) -> list[dict]:
    """Every contradiction between the assessment's own statements, resolved where the evidence can.

    Each entry names both statements, what the evidence said, and whether that settled it. An entry
    marked ``unresolved`` is a report saying "these two disagree and bioAF cannot tell which is
    right", which is honest; two confident opposite statements side by side is not.
    """
    findings: list[dict] = []
    kinds = _kinds_by_text(blocker_kinds)

    check = (precompute_checks or {}).get(CHECK_SAMPLES_DESCRIBED) or {}
    conflicting = [
        b
        for b in (blockers or [])
        if (blocker_kind(b, kinds) == BLOCKER_SAMPLE_ASSIGNMENT)
        or (blocker_kind(b, kinds) is None and _asserts_samples_unrecoverable(b))
    ]
    if check.get("verdict") == OK and conflicting:
        evidence = _sample_metadata_evidence(supplements)
        if evidence is not None:
            findings.append(
                {
                    "topic": "sample_recoverability",
                    "status": RESOLVED,
                    "statement": check.get("detail") or "the paper describes its samples well enough to use",
                    "contradicts": conflicting,
                    "settled_by": (
                        f"{evidence.get('label') or evidence.get('filename')} was retrieved and holds "
                        f"{evidence.get('row_count')} per-sample rows"
                    ),
                    "outcome": "the blocker is superseded by the inspected sample table",
                }
            )
        else:
            candidates = _metadata_candidates(supplements)
            if candidates:
                outcome = (
                    f"Per-sample group assignments are not listed in the text. {', '.join(candidates)} may "
                    f"carry them, but {'it was' if len(candidates) == 1 else 'they were'} not retrieved in this "
                    "attempt"
                )
            else:
                outcome = (
                    "bioAF states both that the samples are described well enough and that the per-sample "
                    "assignments cannot be recovered, and nothing it inspected settles which is right"
                )
            findings.append(
                {
                    "topic": "sample_recoverability",
                    "status": UNRESOLVED,
                    "statement": check.get("detail") or "the paper describes its samples well enough to use",
                    "contradicts": conflicting,
                    "settled_by": None,
                    "outcome": outcome,
                }
            )
    return findings


def apply_resolutions(
    *, precompute_checks: dict | None, blockers: list[str] | None, findings: list[dict]
) -> tuple[dict, list[str]]:
    """Express the dependent statements against the reconciled evidence.

    A resolved contradiction removes the superseded blocker, because leaving it standing beside the
    statement that supersedes it is the defect. An unresolved one leaves BOTH in place and downgrades
    the check to unknown, so the report cannot read as though bioAF had established something it
    could not.
    """
    checks = {k: dict(v) if isinstance(v, dict) else v for k, v in (precompute_checks or {}).items()}
    remaining = list(blockers or [])

    for finding in findings:
        if finding.get("topic") != "sample_recoverability":
            continue
        if finding.get("status") == RESOLVED:
            superseded = set(finding.get("contradicts") or [])
            remaining = [b for b in remaining if b not in superseded]
            check = checks.get(CHECK_SAMPLES_DESCRIBED)
            if isinstance(check, dict):
                check["reconciled_against"] = finding.get("settled_by")
        else:
            check = checks.get(CHECK_SAMPLES_DESCRIBED)
            if isinstance(check, dict):
                check["verdict"] = UNKNOWN
                check["detail"] = finding.get("outcome") or check.get("detail")
                check["unresolved_contradiction"] = True
    return checks, remaining
