"""change_7.2 section 6: validate a claim against the metric's contract, not its name.

Comparing key names catches a contradiction and nothing more. `total_sequences` demonstrates both
gaps at once. Its contract is "raw reads (or read pairs) sequenced per sample, BEFORE trimming or
filtering", and its own definition concedes the parenthesis. So:

- A paper that writes "mean sequencing depth" without qualification matches none of the configured
  conflict words, and an after-trimming figure would score against a pre-trim count.
- A paired-end protocol makes a factor of two available inside a plausible-looking binding, against
  a relative tolerance of 0.25.
- A number that is a total across samples rather than a per-sample mean is wrong by the sample count.

Aggregation, processing stage and the reads-versus-pairs convention are the three axes to check, and
nothing performs any of them today.

**A claim that does not settle an axis is reported with that limitation rather than scored against an
assumption.** It is still shown with its delta: an unstated convention is a gap in what the paper
said, not evidence that the paper is wrong.

**Deliberately narrow.** Only `total_sequences` has a measured contract ambiguity behind it, and a
speculative axis here silently stops a legitimate claim from scoring, which is the opposite of the
failure it exists to prevent. The same discipline the `basis_conflicts` table is held to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ContractAxis:
    """One thing the metric's contract fixes that a paper has to state to be comparable."""

    key: str
    # What a reader is told is missing, in the paper's terms rather than the pipeline's.
    question: str
    # Words that settle the axis EITHER WAY. Settled-and-conflicting is `basis_conflicts`' job; this
    # only asks whether the paper said anything at all.
    settled_by: tuple[str, ...]


_AGGREGATION = ContractAxis(
    key="aggregation",
    question="whether this is a per-sample figure or a total across samples",
    settled_by=(
        "mean",
        "median",
        "average",
        "avg",
        "per sample",
        "per library",
        "per run",
        "each sample",
        "each library",
        "total across",
        "combined across",
        "summed",
        "aggregate",
        "cumulative",
    ),
)

_PROCESSING_STAGE = ContractAxis(
    key="processing_stage",
    question="whether the count is before or after trimming and filtering",
    settled_by=(
        "raw",
        "before trim",
        "pre trim",
        "pretrim",
        "prior to trim",
        "as sequenced",
        "off the sequencer",
        "after trim",
        "post trim",
        "trimmed",
        "filtered",
        "after qc",
        "post qc",
        "clean read",
        "quality filtered",
    ),
)

_READ_UNIT = ContractAxis(
    key="read_unit",
    question="whether the number counts reads or read pairs",
    settled_by=(
        "read pair",
        "pairs",
        "paired end",
        "paired-end",
        "fragment",
        "single end",
        "single-end",
        "se read",
        "mate",
    ),
)

# The contract axes each controlled metric requires the paper to settle. Absent means the metric's
# contract admits no ambiguity a paper could leave open, so nothing is asked of it.
CONTRACT_AXES: dict[str, tuple[ContractAxis, ...]] = {
    "total_sequences": (_AGGREGATION, _PROCESSING_STAGE, _READ_UNIT),
}

_SEPARATORS = re.compile(r"[_\-/]+")


def _normalise(*parts) -> str:
    joined = " ".join(str(p) for p in parts if p)
    return _SEPARATORS.sub(" ", joined).lower()


def unsettled_axes(metric_key: str | None, claim: dict) -> tuple[ContractAxis, ...]:
    """The axes this metric's contract requires that the claim leaves open.

    Everything the paper said about the claim is searched: its key, its unit, its own wording, and
    the structured context the extraction recorded. A convention stated anywhere counts as stated.
    """
    axes = CONTRACT_AXES.get(metric_key or "")
    if not axes:
        return ()
    haystack = _normalise(
        claim.get("metric_key"),
        claim.get("unit"),
        claim.get("claim_text"),
        claim.get("qc_stage"),
        claim.get("sample_subset"),
        claim.get("measurement_basis"),
        claim.get("output_type"),
    )
    return tuple(axis for axis in axes if not any(word in haystack for word in axis.settled_by))


def contract_limitation(metric_key: str | None, axes: tuple[ContractAxis, ...]) -> str:
    """What to tell a reader when the paper leaves an axis of the contract open."""
    if not axes:
        return ""
    questions = "; ".join(axis.question for axis in axes)
    return (
        f"the paper does not state {questions}, which {metric_key} fixes, so this claim is shown "
        "beside bioAF's number rather than scored against it"
    )
