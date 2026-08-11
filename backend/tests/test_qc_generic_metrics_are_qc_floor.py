"""The generic QC engine widens coverage, it does not lower the bar.

Broadening QC extraction to every pipeline type means lit_validation can now
compare a paper's claims for types that previously computed nothing. That must
buy honesty, not verdicts: every metric the generic engine emits is a technical
QC floor (data quality and identity), so a pipeline type covered only by it can
support `inconclusive`, never `validated`.

This is the spec-06 gate. It is easy to erode by accident: adding one
finding-tier metric to the registry would silently let a new pipeline type start
producing green verdicts on read depth alone.
"""

from app.services.qc.multiqc_registry import GENERIC_METRIC_KEYS
from app.services.validation_classifier_service import _tier, classify_study


def _target(metric_key: str, value: float, unit: str | None = None) -> dict:
    return {"metric_key": metric_key, "claimed_value": value, "unit": unit, "tolerance": None}


def test_no_generically_extracted_metric_is_finding_tier():
    for key in GENERIC_METRIC_KEYS:
        assert _tier(key) == "qc_floor", f"{key} would let a generic run earn `validated` on its own"


def test_peak_count_is_still_the_only_finding_tier_qc_metric():
    """Guards the inverse: the gate is meaningful only while something is
    finding-tier. If this changes, spec-06 was revisited and this test should
    change deliberately."""
    assert _tier("peak_count") == "finding"


def test_full_generic_agreement_is_inconclusive_not_validated():
    """Every generic metric agreeing with the paper still is not a validation:
    it proves the data is real and processed cleanly, not that any reported
    finding held up."""
    result = classify_study(
        [
            _target("total_reads", 6_700_000),
            _target("alignment_rate", 99.9, unit="%"),
            _target("duplication_rate", 20.7, unit="%"),
            _target("gc_content", 43.0, unit="%"),
        ],
        {
            "total_sequences": 6_677_908,
            "reads_mapped_genome": 0.9988,
            "percent_duplicates": 20.7,
            "percent_gc": 43.0,
        },
        mapping_confidence="exact",
        reference_genome="GRCh38",
    )

    assert result["classification"] == "inconclusive"
    assert result["auto_finalize"] is False
    assert result["coverage"]["finding_agree"] == 0


def test_the_reason_says_plainly_that_only_qc_was_checked():
    """A scientist reading `inconclusive` needs to know it means "we checked the
    processing, not the finding", not "something looked wrong"."""
    result = classify_study(
        [_target("total_reads", 6_700_000)],
        {"total_sequences": 6_677_908},
        mapping_confidence="exact",
        reference_genome="GRCh38",
    )

    reasoning = " ".join(result["reasoning"]) if isinstance(result["reasoning"], list) else result["reasoning"]
    assert "qc" in reasoning.lower()


def test_generic_metrics_still_surface_a_real_divergence():
    """Coverage that cannot earn `validated` must still be able to catch a
    problem: a read depth an order of magnitude off the paper is a real signal
    even though it is a floor metric."""
    result = classify_study(
        [_target("total_reads", 60_000_000)],
        {"total_sequences": 6_677_908},
        mapping_confidence="exact",
        reference_genome="GRCh38",
    )

    assert result["classification"] != "validated"
    assert result["coverage"]["diverge"] == 1
