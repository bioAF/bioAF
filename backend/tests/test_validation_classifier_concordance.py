"""E4' classifier extension: Level-3 finding concordance (lit_validation, ADR-069).

A concordance `agree` is a finding-tier agreement that can earn a Level-3 `validated`; a
concordance `diverge` routes through the same attribution guard. With no concordance the
verdict is exactly the Level-2 behavior (covered by test_validation_classifier).
"""

from app.services.validation_classifier_service import classify_study


def _conc(verdict, **kw):
    base = {
        "verdict": verdict,
        "kind": "gene",
        "paper_n": 100,
        "our_n": 90,
        "overlap": 85,
        "concordant": 82,
        "directional_overlap_frac": 0.82,
        "enrichment_p": 1e-30,
        "notes": [],
    }
    base.update(kw)
    return base


def test_concordance_agree_validates_level3():
    r = classify_study([], {}, concordance_results=[_conc("agree")])
    assert r["classification"] == "validated"
    # a Level-3 validated is consequential + thresholds are first-pass -> held for a human
    assert r["auto_finalize"] is False
    assert r["coverage"]["concordance_agree"] == 1
    assert "finding" in r["reasoning"].lower()


def test_concordance_diverge_cleared_is_not_validated():
    r = classify_study(
        [],
        {},
        mapping_confidence="high",
        reference_genome="GRCh38",
        concordance_results=[_conc("diverge", directional_overlap_frac=0.1, enrichment_p=0.9, concordant=8)],
    )
    assert r["classification"] == "not_validated"
    assert r["auto_finalize"] is False


def test_concordance_diverge_uncleared_is_inconclusive():
    r = classify_study(
        [],
        {},
        mapping_confidence="partial",
        reference_genome="GRCh38",
        concordance_results=[_conc("diverge", directional_overlap_frac=0.1, enrichment_p=0.9)],
    )
    assert r["classification"] == "inconclusive"


def test_concordance_not_computed_is_coverage_gap():
    r = classify_study([], {}, concordance_results=[_conc("not_computed")])
    assert r["classification"] == "inconclusive"
    assert "coverage gap" in r["reasoning"].lower()


def test_concordance_agree_satisfies_finding_gate_over_floor():
    # a QC-floor metric agrees, but NO scalar finding agrees. Without concordance this is the
    # floor-only inconclusive gate (spec-06); a concordance agree lifts it to a Level-3 validated.
    targets = [{"metric_key": "reads_mapped_genome", "claimed_value": 0.96, "unit": "", "tolerance": None}]
    computed = {"reads_mapped_genome": 0.97}

    floor_only = classify_study(targets, computed)
    assert floor_only["classification"] == "inconclusive"  # the spec-06 gate

    with_finding = classify_study(targets, computed, concordance_results=[_conc("agree")])
    assert with_finding["classification"] == "validated"


def test_no_concordance_leaves_level2_behavior_unchanged():
    # a finding-tier scalar agreement with no concordance -> validated, exactly as before
    targets = [{"metric_key": "peak_count", "claimed_value": 16000, "unit": "", "tolerance": None}]
    computed = {"peak_count": 16400}
    r = classify_study(targets, computed)
    assert r["classification"] == "validated"
    assert r["coverage"]["concordance"] == 0
