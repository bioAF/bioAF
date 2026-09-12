"""change_7.5 section 1.4: a declined QC binding closes only the QC check.

Study 38's four differential-expression counts each read "No supported metric: bioAF has no metric that
measures this claim, so the mapping was declined and the claim cannot be compared." The model was right
to decline them as QC metrics, and that decides the QC check alone. The sentence also said the claims
could not be compared at all, which their deposited author tables contradict. And a model's decline
still reached a comparison through the alias table, contradicting the report's own wording.
"""

from app.services.validation_classifier_service import compare_targets
from app.services.validation_report_summary import summarize


def _declined(**extra) -> dict:
    return {
        "metric_key": "total_genes_detected",
        "claim_text": "We detected 10,500 genes",
        "claimed_value": 10500,
        "bound_key": None,
        "bound_by": "model",
        "binding_reason": "a subset of the samples",
        **extra,
    }


def test_a_claim_the_model_declined_is_never_compared_through_the_alias_table():
    """`total_genes_detected` is in the alias table, so the decline used to be overruled by a lookup."""
    [row] = compare_targets([_declined()], {"total_genes_detected": 10500})
    assert row["mapped_key"] is None
    # No QC metric is this claim, which is the verdict a claim with no counterpart has always had.
    assert row["verdict"] == "not_computed"
    assert row["computed_value"] is None


def test_a_legacy_claim_nobody_bound_still_resolves_through_the_alias_table():
    [row] = compare_targets([_declined(bound_by="alias_table")], {"total_genes_detected": 10500})
    assert row["mapped_key"] == "total_genes_detected"


def test_a_row_from_before_the_binding_column_still_resolves_through_the_alias_table():
    [row] = compare_targets([_declined(bound_by=None)], {"total_genes_detected": 10500})
    assert row["mapped_key"] == "total_genes_detected"


def test_the_declined_claim_is_worded_for_the_qc_check_only():
    summary = summarize(study={"state": "classified"}, evidence={}, plan={}, targets=[_declined()], issues=[])
    mapping = summary["claims"][0]["mapping"]
    assert mapping["status"] == "no_supported_metric"
    assert mapping["label"] == "No QC metric measures this claim"
    assert mapping["explanation"] == (
        "bioAF computes no QC metric that measures this claim, so it cannot be compared as a QC metric. "
        "This does not decide its other checks."
    )
    assert "cannot be compared." not in mapping["explanation"].replace("as a QC metric.", "")
