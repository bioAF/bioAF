"""change_7.2 section 6: validate a claim against the metric's contract, not its name.

`total_sequences` is "raw reads (or read pairs) sequenced per sample, BEFORE trimming or filtering",
and its own definition concedes the parenthesis. Groff's "44.6 million reads per library (mean)"
matches the key perfectly and settles none of the three axes that contract fixes. Study 32 declined
the binding and study 33 made it; neither outcome is validated, because nothing checked aggregation,
processing stage or the reads-versus-pairs convention.
"""

from app.services.validation_classifier_service import compare_targets
from app.services.validation_metric_contract import CONTRACT_AXES, contract_limitation, unsettled_axes


class TestTheContractIsExplicit:
    def test_the_depth_metric_declares_the_three_axes(self):
        keys = {axis.key for axis in CONTRACT_AXES["total_sequences"]}
        assert keys == {"aggregation", "processing_stage", "read_unit"}

    def test_a_metric_with_no_declared_axes_asks_nothing(self):
        assert unsettled_axes("percent_gc", {"unit": "%"}) == ()

    def test_the_table_stays_narrow(self):
        """A speculative axis silently stops a legitimate claim from scoring, which is the opposite
        of the failure it exists to prevent."""
        assert set(CONTRACT_AXES) == {"total_sequences"}


class TestAnUnstatedAxisIsALimitationNotAScore:
    def test_mean_sequencing_depth_leaves_the_processing_stage_open(self):
        axes = unsettled_axes("total_sequences", {"metric_key": "sequencing_depth", "unit": "reads per library"})
        assert "processing_stage" in {a.key for a in axes}

    def test_it_leaves_the_read_unit_open_too(self):
        axes = unsettled_axes("total_sequences", {"metric_key": "sequencing_depth", "unit": "reads per library"})
        assert "read_unit" in {a.key for a in axes}

    def test_a_mean_settles_the_aggregation(self):
        axes = unsettled_axes(
            "total_sequences", {"metric_key": "mean_reads_per_sample", "unit": "reads per library (mean)"}
        )
        assert "aggregation" not in {a.key for a in axes}

    def test_a_paper_that_states_every_axis_leaves_nothing_open(self):
        axes = unsettled_axes(
            "total_sequences",
            {
                "metric_key": "mean_raw_reads_per_sample",
                "unit": "read pairs",
                "claim_text": "a mean of 44.6 million raw read pairs per sample before trimming",
            },
        )
        assert axes == ()

    def test_the_claims_own_wording_counts_as_stating_it(self):
        axes = unsettled_axes(
            "total_sequences",
            {"metric_key": "sequencing_depth", "claim_text": "median raw reads per library, single-end"},
        )
        assert axes == ()

    def test_the_limitation_names_what_is_missing(self):
        axes = unsettled_axes("total_sequences", {"metric_key": "sequencing_depth"})
        message = contract_limitation("total_sequences", axes)
        assert "before or after trimming" in message
        assert "reads or read pairs" in message


class TestTheComparisonActsOnIt:
    def test_an_unsettled_claim_is_shown_but_not_scored(self):
        rows = compare_targets(
            [{"metric_key": "sequencing_depth", "claimed_value": 44_600_000, "unit": "reads per library"}],
            {"total_sequences": 22_300_000},
        )
        assert rows[0]["advisory"] is True
        assert rows[0]["advisory_reason"]

    def test_the_paper_s_number_is_still_reported_beside_ours(self):
        """An unstated convention is a gap in what the paper said, not evidence that it is wrong."""
        rows = compare_targets(
            [{"metric_key": "sequencing_depth", "claimed_value": 44_600_000, "unit": "reads per library"}],
            {"total_sequences": 22_300_000},
        )
        assert rows[0]["claimed_value"] == 44_600_000
        assert rows[0]["computed_value"] == 22_300_000

    def test_the_open_axes_are_named_on_the_row(self):
        rows = compare_targets(
            [{"metric_key": "sequencing_depth", "claimed_value": 44_600_000, "unit": "reads per library"}],
            {"total_sequences": 22_300_000},
        )
        assert set(rows[0]["unsettled_contract_axes"]) >= {"processing_stage", "read_unit"}

    def test_a_factor_of_two_cannot_hide_inside_a_plausible_binding(self):
        """A paired-end protocol puts a factor of two inside a relative tolerance of 0.25, and the
        claim would have scored as a divergence attributed to the paper."""
        rows = compare_targets(
            [{"metric_key": "sequencing_depth", "claimed_value": 44_600_000, "unit": "reads per library"}],
            {"total_sequences": 22_300_000},
        )
        assert rows[0]["verdict"] != "diverge" or rows[0]["advisory"] is True

    def test_a_fully_stated_claim_still_scores(self):
        rows = compare_targets(
            [
                {
                    "metric_key": "mean_raw_reads_per_sample",
                    "claimed_value": 44_600_000,
                    "unit": "read pairs",
                    "claim_text": "a mean of 44.6 million raw read pairs per sample before trimming",
                }
            ],
            {"total_sequences": 44_000_000},
        )
        assert rows[0]["advisory"] is False
        assert rows[0]["verdict"] == "agree"

    def test_name_level_checking_still_catches_a_real_conflict(self):
        """The cheap secondary signal is kept: a claim that STATES a different basis is still
        refused on that basis, not on an unstated axis."""
        rows = compare_targets(
            [
                {
                    "metric_key": "mean_reads_per_sample",
                    "claimed_value": 30_000_000,
                    "unit": "reads after trimming",
                    "claim_text": "a mean of 30 million reads per sample after trimming, paired-end",
                }
            ],
            {"total_sequences": 44_000_000},
        )
        assert rows[0]["advisory"] is True
        assert "trim" in (rows[0]["advisory_reason"] or "")
