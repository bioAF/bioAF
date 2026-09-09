"""change_7.1 section 7: what a measurement is measured PER.

Study 32 bound Groff's "44.6 million reads per library (mean)" to ``mean_reads_per_cell``. One
library there is one whole embryo, so against a pipeline's per-cell depth the claim is wrong by the
number of cells in an embryo, and the gap would have been reported as the paper diverging from its
own published result.

**This is a contract fact, not a scientific judgement**, so it is checked deterministically. The
basis was sitting in the claim's unit string the whole time ("reads per library (mean)"), and the
metric's own key states its basis in its name (``mean_reads_per_cell``). Reading both and comparing
them needs no model and cannot hallucinate.

**Not knowing is not disagreeing.** A claim whose unit does not spell out a basis is not refused;
refusing there would block every claim written in ordinary prose.
"""

from __future__ import annotations

import re

# The units a quantity can be measured against. Ordered longest-first so "per cell" cannot match
# inside "per cell line".
BASES = ("cell", "library", "sample", "subject", "cohort", "run", "lane")

# Matched against a separator-normalised string. `\b` does not fire between "per" and "_cell",
# because an underscore is a word character, so `mean_reads_per_cell` never matched until the
# underscores became spaces.
_PER_RE = re.compile(r"\bper\s+(" + "|".join(BASES) + r")s?\b", re.I)
_ACROSS_RE = re.compile(r"\bacross\s+(?:the\s+)?(" + "|".join(BASES) + r")s?\b", re.I)


def basis_of(text: str | None) -> str | None:
    """The basis a unit string or metric key states, or None when it states none.

    Handles both shapes a basis actually arrives in: "reads per library (mean)" and
    ``mean_reads_per_cell``, which is the same statement with underscores.
    """
    if not text:
        return None
    normalised = str(text).replace("_", " ")
    for pattern in (_PER_RE, _ACROSS_RE):
        match = pattern.search(normalised)
        if match:
            return match.group(1).lower()
    return None


def basis_conflicts(*, claim_basis: str | None, metric_key: str | None) -> bool:
    """Whether a claim's basis contradicts the basis the controlled metric is computed on.

    Both must be known for this to be a conflict. An unknown claim basis is a gap in what the paper
    said, not evidence that the two disagree.
    """
    metric_basis = basis_of(metric_key)
    if not claim_basis or not metric_basis:
        return False
    return claim_basis.lower() != metric_basis
