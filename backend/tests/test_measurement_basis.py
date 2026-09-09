"""change_7.1 section 7: a number measured per library is not a number measured per cell.

Study 32 bound Groff's "44.6 million reads per library (mean)" to `mean_reads_per_cell`. One
library is one whole embryo. Compared against a pipeline's per-cell depth the claim is wrong by
the number of cells in an embryo, and the gap would have been reported as the paper diverging from
its own result.

The basis was in the claim's unit string the whole time. Deterministic to read, and a deterministic
check is what should refuse the comparison: this is a contract fact, not a scientific judgement.
"""

import pytest

from app.services.validation_classifier_service import compare_targets
from app.services.validation_measurement_basis import basis_of, basis_conflicts


class TestReadingTheBasisFromTheClaim:
    @pytest.mark.parametrize(
        "unit,expected",
        [
            ("reads per library (mean)", "library"),
            ("mean reads per cell", "cell"),
            ("reads per sample", "sample"),
            ("counts per subject", "subject"),
            ("total reads across the cohort", "cohort"),
            ("genes", None),
            ("", None),
            (None, None),
        ],
    )
    def test_basis_of(self, unit, expected):
        assert basis_of(unit) == expected

    def test_the_metric_key_is_read_too(self):
        """The key carries a basis of its own: `mean_reads_per_cell` says per cell in its name."""
        assert basis_of("mean_reads_per_cell") == "cell"


class TestTheComparisonRefusesABasisMismatch:
    def test_per_library_against_a_per_cell_metric_is_refused(self):
        assert basis_conflicts(claim_basis="library", metric_key="mean_reads_per_cell") is True

    def test_a_matching_basis_is_not_a_conflict(self):
        assert basis_conflicts(claim_basis="cell", metric_key="mean_reads_per_cell") is False

    def test_an_unknown_claim_basis_is_not_asserted_to_conflict(self):
        """Not knowing is not disagreeing. Refusing here would block every claim whose unit does
        not spell out its basis."""
        assert basis_conflicts(claim_basis=None, metric_key="mean_reads_per_cell") is False

    def test_a_metric_with_no_basis_in_its_name_is_not_a_conflict(self):
        assert basis_conflicts(claim_basis="library", metric_key="total_genes_detected") is False


class TestTheVerdictSaysItWasNotCompared:
    def test_a_basis_mismatch_is_not_given_a_number_verdict(self):
        rows = compare_targets(
            [
                {
                    "metric_key": "mean_reads_per_cell",
                    "claimed_value": 44_600_000,
                    "unit": "reads per library (mean)",
                    "measurement_basis": "library",
                    "bound_key": "mean_reads_per_cell",
                }
            ],
            {"mean_reads_per_cell": 50_000},
        )
        assert rows[0]["verdict"] == "not_compared"

    def test_the_reason_names_both_bases(self):
        rows = compare_targets(
            [
                {
                    "metric_key": "mean_reads_per_cell",
                    "claimed_value": 44_600_000,
                    "unit": "reads per library (mean)",
                    "measurement_basis": "library",
                    "bound_key": "mean_reads_per_cell",
                }
            ],
            {"mean_reads_per_cell": 50_000},
        )
        reason = rows[0]["advisory_reason"] or ""
        assert "library" in reason and "cell" in reason

    def test_a_matching_basis_still_compares(self):
        rows = compare_targets(
            [
                {
                    "metric_key": "mean_reads_per_cell",
                    "claimed_value": 50_000,
                    "unit": "mean reads per cell",
                    "measurement_basis": "cell",
                    "bound_key": "mean_reads_per_cell",
                }
            ],
            {"mean_reads_per_cell": 50_000},
        )
        assert rows[0]["verdict"] not in (None, "not_compared")
