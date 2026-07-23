"""E6: finding-concordance comparator (lit_validation Level-3).

Compares OUR reproduced finding set against the paper's deposited finding set (both
normalized by result_set_normalizer) and returns a deterministic concordance verdict.
Per ADR-069 the verdict requires BOTH signals to clear a threshold: directional overlap
(the fraction of the paper's significant hits we recover with concordant direction) AND
an enrichment significance of the overlap (hypergeometric). Two families: gene-set
(RNA-seq, id equality) and genomic-interval-set (ATAC/ChIP, reciprocal overlap).

The hypergeometric survival function is computed with math.lgamma so this has no numeric
dependency (scipy is not a declared backend requirement). The comparator is pure logic; the
classifier (E4') consumes its result and the attribution guard (E3') clears our side before a
divergence can become not_validated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.services.result_set_normalizer import FindingSet

_DEFAULT_OVERLAP_THRESHOLD = 0.5
_DEFAULT_ENRICHMENT_ALPHA = 0.05
_DEFAULT_RECIPROCAL_FRACTION = 0.5


@dataclass
class ConcordanceResult:
    kind: str  # "gene" | "interval"
    verdict: str  # "agree" | "diverge" | "not_computed"
    paper_n: int = 0
    our_n: int = 0
    overlap: int = 0
    concordant: int = 0
    directional_overlap_frac: float = 0.0
    enrichment_p: float = 1.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "verdict": self.verdict,
            "paper_n": self.paper_n,
            "our_n": self.our_n,
            "overlap": self.overlap,
            "concordant": self.concordant,
            "directional_overlap_frac": round(self.directional_overlap_frac, 4),
            "enrichment_p": self.enrichment_p,
            "notes": self.notes,
        }


def _log_choose(a: int, b: int) -> float:
    if b < 0 or b > a:
        return float("-inf")
    return math.lgamma(a + 1) - math.lgamma(b + 1) - math.lgamma(a - b + 1)


def _hypergeom_sf(k: int, N: int, K: int, n: int) -> float:
    """P(X >= k) for X ~ Hypergeometric(N, K, n). Dependency-free.

    N = universe (features tested), K = paper hits, n = our hits, k = observed overlap.
    """
    if k <= 0:
        return 1.0
    if N <= 0 or K <= 0 or n <= 0:
        return 1.0
    log_denom = _log_choose(N, n)
    if log_denom == float("-inf"):
        return 1.0
    total = 0.0
    upper = min(K, n)
    for i in range(k, upper + 1):
        term = _log_choose(K, i) + _log_choose(N - K, n - i) - log_denom
        if term != float("-inf"):
            total += math.exp(term)
    return min(1.0, total)


def _verdict(directional_frac: float, enrichment_p: float, overlap_threshold: float, alpha: float) -> str:
    return "agree" if (directional_frac >= overlap_threshold and enrichment_p <= alpha) else "diverge"


def compare_gene_sets(
    paper: FindingSet,
    ours: FindingSet,
    universe: int,
    *,
    overlap_threshold: float = _DEFAULT_OVERLAP_THRESHOLD,
    enrichment_alpha: float = _DEFAULT_ENRICHMENT_ALPHA,
) -> ConcordanceResult:
    paper_dirs = paper.directions()
    our_dirs = ours.directions()
    res = ConcordanceResult(kind="gene", verdict="not_computed", paper_n=len(paper_dirs), our_n=len(our_dirs))

    if not paper_dirs or not our_dirs:
        res.notes.append("empty finding set on one side; nothing to compare")
        return res

    # namespace reconciliation: comparing symbol vs Ensembl without mapping yields a
    # spurious zero overlap; surface it as not_computed (attribution uncleared), not diverge.
    if paper.namespace != ours.namespace and "unknown" not in (paper.namespace, ours.namespace):
        res.notes.append(f"namespace mismatch (paper={paper.namespace}, ours={ours.namespace}); id-mapping required")
        return res

    shared = set(paper_dirs) & set(our_dirs)
    concordant = [g for g in shared if paper_dirs[g] is not None and paper_dirs[g] == our_dirs[g]]
    universe = max(universe, len(paper_dirs), len(our_dirs))

    res.overlap = len(shared)
    res.concordant = len(concordant)
    res.directional_overlap_frac = len(concordant) / len(paper_dirs)
    res.enrichment_p = _hypergeom_sf(len(shared), universe, len(paper_dirs), len(our_dirs))
    res.verdict = _verdict(res.directional_overlap_frac, res.enrichment_p, overlap_threshold, enrichment_alpha)
    return res


def _parse_interval(entity_id: str) -> tuple[str, int, int] | None:
    m = entity_id.rsplit(":", 1)
    if len(m) != 2 or "-" not in m[1]:
        return None
    chrom = m[0]
    try:
        start_s, end_s = m[1].split("-", 1)
        return chrom, int(start_s), int(end_s)
    except ValueError:
        return None


def _reciprocal_overlap(a: tuple[int, int], b: tuple[int, int], fraction: float) -> bool:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    if inter <= 0:
        return False
    shorter = min(a[1] - a[0], b[1] - b[0])
    if shorter <= 0:
        return False
    return inter / shorter >= fraction


def compare_interval_sets(
    paper: FindingSet,
    ours: FindingSet,
    universe: int,
    *,
    reciprocal_fraction: float = _DEFAULT_RECIPROCAL_FRACTION,
    overlap_threshold: float = _DEFAULT_OVERLAP_THRESHOLD,
    enrichment_alpha: float = _DEFAULT_ENRICHMENT_ALPHA,
) -> ConcordanceResult:
    res = ConcordanceResult(
        kind="interval", verdict="not_computed", paper_n=len(paper.entities), our_n=len(ours.entities)
    )
    if not paper.entities or not ours.entities:
        res.notes.append("empty finding set on one side; nothing to compare")
        return res

    # index our intervals by chromosome
    ours_by_chrom: dict[str, list[tuple[int, int, str | None]]] = {}
    for e in ours.entities:
        parsed = _parse_interval(e.id)
        if parsed is None:
            continue
        chrom, start, end = parsed
        ours_by_chrom.setdefault(chrom, []).append((start, end, e.direction))

    matched = 0
    concordant = 0
    for e in paper.entities:
        parsed = _parse_interval(e.id)
        if parsed is None:
            continue
        chrom, start, end = parsed
        best = None
        for os, oe, odir in ours_by_chrom.get(chrom, []):
            if _reciprocal_overlap((start, end), (os, oe), reciprocal_fraction):
                best = odir
                break
        if best is not None:
            matched += 1
            if e.direction is not None and e.direction == best:
                concordant += 1

    universe = max(universe, len(paper.entities), len(ours.entities))
    res.overlap = matched
    res.concordant = concordant
    res.directional_overlap_frac = concordant / len(paper.entities)
    res.enrichment_p = _hypergeom_sf(matched, universe, len(paper.entities), len(ours.entities))
    res.verdict = _verdict(res.directional_overlap_frac, res.enrichment_p, overlap_threshold, enrichment_alpha)
    return res
