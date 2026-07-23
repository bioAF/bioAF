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


def test_gene_diverge_low_overlap():
    paper = _genes([(f"g{i}", "up") for i in range(20)])
    ours = _genes([("g0", "up"), ("x1", "up")])
    r = compare_gene_sets(paper, ours, universe=20000)
    assert r.verdict == "diverge"
    assert r.directional_overlap_frac == 1 / 20


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
