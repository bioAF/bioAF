"""E6 finding-concordance comparator (lit_validation Level-3, C2).

Directional overlap + hypergeometric enrichment, both required for `agree`. Gene-set
(id equality) and genomic-interval-set (reciprocal overlap) families.
"""

import math

from app.services.result_set_normalizer import FindingEntity, FindingSet
from app.services.validation_concordance_service import (
    _hypergeom_sf,
    compare_gene_sets,
    compare_interval_sets,
)


def _genes(pairs, namespace="symbol"):
    return FindingSet(
        kind="gene",
        namespace=namespace,
        entities=[FindingEntity(id=g, direction=d) for g, d in pairs],
    )


def _intervals(items):
    return FindingSet(
        kind="interval",
        namespace="interval",
        entities=[FindingEntity(id=i, direction=d) for i, d in items],
    )


def test_gene_agree_full_recovery():
    paper = _genes([("A", "up"), ("B", "up"), ("C", "down"), ("D", "up")])
    ours = _genes([("A", "up"), ("B", "up"), ("C", "down"), ("D", "up"), ("E", "up")])
    r = compare_gene_sets(paper, ours, universe=20000)
    assert r.verdict == "agree"
    assert r.directional_overlap_frac == 1.0
    assert r.enrichment_p <= 0.05


def test_gene_low_recovery_but_enrichment_clears_is_partial():
    # Under the 3-way taxonomy (ADR-069) a low recovery is NOT automatically a divergence: recovering
    # 1 of the paper's 20 hits from just 2 of our own is still ~500x enriched over chance (p ~ 2e-3),
    # so the overlap is real, just thin. That is `partial` (held for a human), superseding the old
    # 2-way `diverge`. The alpha/agree cutoffs are the C5 calibration lever (spec-08).
    paper = _genes([(f"g{i}", "up") for i in range(20)])
    ours = _genes([("g0", "up"), ("x1", "up")])
    r = compare_gene_sets(paper, ours, universe=20000)
    assert r.directional_overlap_frac == 1 / 20
    assert r.enrichment_p <= 0.05
    assert r.verdict == "partial"


def test_gene_partial_enrichment_real_recovery_short():
    # The strong-but-partial case (study 6): the overlap is unmistakably NOT coincidence
    # (enrichment clears) but we recover well under the agree threshold of the paper's hits.
    # This is `partial`, distinct from a `diverge` where the overlap is no better than chance.
    paper = _genes([(f"g{i}", "up") for i in range(20)])
    ours = _genes([("g0", "up"), ("g1", "up"), ("g2", "up"), ("g3", "up"), ("g4", "up"), ("x1", "up"), ("x2", "up")])
    r = compare_gene_sets(paper, ours, universe=20000)
    assert r.directional_overlap_frac == 5 / 20  # below the 0.5 agree threshold
    assert r.enrichment_p <= 0.05  # but the overlap is statistically real, not coincidence
    assert r.verdict == "partial"


def test_gene_diverge_when_overlap_is_coincidence():
    # Recovery short AND the overlap is no better than chance (enrichment does not clear) -> diverge,
    # NOT partial. Enrichment significance is the guard that keeps `partial` from being gamed by luck.
    paper = _genes([(f"g{i}", "up") for i in range(200)])
    ours = _genes([("g0", "up")] + [(f"x{i}", "up") for i in range(400)])
    r = compare_gene_sets(paper, ours, universe=20000)
    assert r.directional_overlap_frac < 0.5
    assert r.enrichment_p > 0.05
    assert r.verdict == "diverge"


def test_gene_direction_discordance_is_not_concordant():
    paper = _genes([("A", "up"), ("B", "up")])
    ours = _genes([("A", "down"), ("B", "down")])
    r = compare_gene_sets(paper, ours, universe=20000)
    assert r.overlap == 2
    assert r.concordant == 0
    assert r.verdict == "diverge"


def test_gene_namespace_mismatch_is_not_computed():
    paper = _genes([("TP53", "up")], namespace="symbol")
    ours = _genes([("ENSG00000141510", "up")], namespace="ensembl_gene")
    r = compare_gene_sets(paper, ours, universe=20000)
    assert r.verdict == "not_computed"
    assert any("namespace" in n.lower() for n in r.notes)


def test_gene_empty_is_not_computed():
    r = compare_gene_sets(_genes([]), _genes([("A", "up")]), universe=20000)
    assert r.verdict == "not_computed"


def test_interval_agree_reciprocal_overlap():
    paper = _intervals([("chr1:1000-2000", "up"), ("chr2:5000-6000", "down")])
    ours = _intervals([("chr1:1100-2100", "up"), ("chr2:4900-5900", "down")])
    r = compare_interval_sets(paper, ours, universe=30000)
    assert r.overlap == 2
    assert r.concordant == 2
    assert r.verdict == "agree"


def test_interval_no_overlap_diverges():
    paper = _intervals([("chr1:1000-2000", "up")])
    ours = _intervals([("chr9:1000-2000", "up")])
    r = compare_interval_sets(paper, ours, universe=30000)
    assert r.overlap == 0
    assert r.verdict == "diverge"


def test_interval_partial_enrichment_real_recovery_short():
    # ATAC/ChIP analogue of the strong-but-partial gene case: several peaks recover with concordant
    # direction and the overlap enrichment clears, but the recovered fraction is below the agree line.
    paper = _intervals([(f"chr1:{1000 + i * 1000}-{2000 + i * 1000}", "up") for i in range(10)])
    ours = _intervals(
        [(f"chr1:{1100 + i * 1000}-{2100 + i * 1000}", "up") for i in range(3)]
        + [("chr9:1000-2000", "up"), ("chr9:3000-4000", "up")]
    )
    r = compare_interval_sets(paper, ours, universe=30000)
    assert r.concordant == 3
    assert r.directional_overlap_frac == 3 / 10  # below the 0.5 agree threshold
    assert r.enrichment_p <= 0.05
    assert r.verdict == "partial"


def test_interval_below_reciprocal_fraction_not_matched():
    # tiny overlap (100bp) vs 1000bp peaks -> reciprocal fraction 0.1 < 0.5 default
    paper = _intervals([("chr1:1000-2000", "up")])
    ours = _intervals([("chr1:1900-2900", "up")])
    r = compare_interval_sets(paper, ours, universe=30000)
    assert r.overlap == 0


def test_hypergeom_sf_matches_scipy():
    from scipy.stats import hypergeom  # dev-only cross-check

    for N, K, n, k in [(20000, 200, 180, 90), (100, 10, 12, 4), (30000, 2, 2, 2)]:
        mine = _hypergeom_sf(k, N, K, n)
        ref = float(hypergeom.sf(k - 1, N, K, n))
        assert math.isclose(mine, ref, rel_tol=1e-6, abs_tol=1e-12), (N, K, n, k, mine, ref)
