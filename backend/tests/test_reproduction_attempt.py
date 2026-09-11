"""change_7.3 section 5: whether reproduction was attempted is its own fact.

`inconclusive` rendered "Could Not Reproduce" whether anything ran or not, so study 34, which
executed nothing, read as a reproduction that failed. Resolving, retrieving or inspecting resources
is never an attempt, and neither is a consistency check against a published results table: only an
analysis executed on acquired inputs is.
"""

import pytest

from app.services.validation_reproduction_attempt import ATTEMPTED, NOT_ATTEMPTED, reproduction_attempt


class TestOnlyExecutionCounts:
    def test_nothing_recorded_is_not_attempted(self):
        assert reproduction_attempt({}, analysis_run_id=None)["status"] == NOT_ATTEMPTED

    def test_inspected_attachments_are_not_an_attempt(self):
        evidence = {
            "supplements": [{"label": "Supplemental File S3", "resolved": True, "role": "results_table"}],
            "assessment": {"supplements_inspected": 1},
        }
        assert reproduction_attempt(evidence, analysis_run_id=None)["status"] == NOT_ATTEMPTED

    def test_a_consistency_check_is_not_an_attempt(self):
        evidence = {"assessment": {"contradictions": [{"status": "resolved"}]}, "reconciliation": {"revisions": [1]}}
        assert reproduction_attempt(evidence, analysis_run_id=None)["status"] == NOT_ATTEMPTED

    def test_an_acquired_deposit_with_nothing_run_on_it_is_not_an_attempt(self):
        """The plan's open question 2, taken as the plan recommends: acquiring inputs and running
        nothing on them is an acquisition, not a reproduction."""
        evidence = {"deposit": {"files": [{"name": "GSE1_counts.txt"}]}}
        assert reproduction_attempt(evidence, analysis_run_id=None)["status"] == NOT_ATTEMPTED

    def test_an_analysis_pipeline_run_is_an_attempt(self):
        assert reproduction_attempt({}, analysis_run_id=42)["status"] == ATTEMPTED

    def test_a_template_notebook_run_is_an_attempt(self):
        assert reproduction_attempt({"level3_run_session_id": 7}, analysis_run_id=None)["status"] == ATTEMPTED

    @pytest.mark.parametrize(
        "outcome", ["dependency_unresolvable", "code_error", "ran_output_diverges", "ran_no_output"]
    )
    def test_code_that_was_launched_is_an_attempt(self, outcome):
        evidence = {"code_execution": {"outcome": outcome, "method": "authors_code"}}
        assert reproduction_attempt(evidence, analysis_run_id=None)["status"] == ATTEMPTED

    @pytest.mark.parametrize("outcome", ["code_absent", "code_unreachable", "generation_failed"])
    def test_code_that_never_launched_is_not(self, outcome):
        evidence = {"code_execution": {"outcome": outcome}}
        assert reproduction_attempt(evidence, analysis_run_id=None)["status"] == NOT_ATTEMPTED

    def test_it_says_what_executed(self):
        attempt = reproduction_attempt({"level3_run_session_id": 7}, analysis_run_id=42)
        assert set(attempt["executed"]) == {"analysis pipeline run", "reproduction notebook"}
