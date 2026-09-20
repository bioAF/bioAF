"""plan_8_5 section 3.5: the route for the two obligations that can only be settled by running.

C1.B (the source loads) and C2.B (its dependencies resolve together) execute untrusted code, so they
are verified only from a RECORDED result of the isolated execution path, under the approval that
path requires. What was missing is the route itself: nothing could ever produce such a record, so
the two obligations were permanently grey with no way forward.

This is the request a person approves, the limits it runs under, and where its result lands. Nothing
here runs anything: submitting the request is the isolated path's business, and a study with no
approved run keeps both obligations untested.
"""

import pytest

from app.services.validation_environment_check import (
    EnvironmentCheckRefused,
    environment_check_request,
    record_environment_check,
)

_PY = {"path": "analysis.py", "language": "python", "text": "import numpy as np\nprint(np.mean([1, 2]))\n"}
_R = {"path": "analysis.R", "language": "r", "text": "library(DESeq2)\nprint(1)\n"}


class TestTheRequestAPersonApproves:
    def test_it_names_what_would_run_and_under_what_limits(self):
        request = environment_check_request(sources=[_PY], manifests=[{"path": "requirements.txt", "text": "numpy==1.26.4\n"}])
        assert request["language"] == "python"
        assert request["files"] == ["analysis.py", "requirements.txt"]
        assert request["limits"]["network"] == "denied"
        assert request["limits"]["timeout_seconds"] > 0
        assert request["approval"]["required"] is True

    def test_it_says_what_each_obligation_would_establish(self):
        request = environment_check_request(sources=[_PY], manifests=[])
        assert set(request["establishes"]) == {"C1.B", "C2.B"}

    def test_an_r_analysis_asks_for_an_r_runtime(self):
        request = environment_check_request(sources=[_R], manifests=[{"path": "renv.lock", "text": "{}"}])
        assert request["language"] == "r"
        assert "R" in request["runtime"]

    def test_it_never_evaluates_the_source_while_building_the_request(self):
        exploding = {"path": "x.py", "language": "python", "text": "raise SystemExit('should never run')\n"}
        assert environment_check_request(sources=[exploding], manifests=[])["files"] == ["x.py"]

    def test_with_no_source_there_is_nothing_to_approve(self):
        with pytest.raises(EnvironmentCheckRefused):
            environment_check_request(sources=[], manifests=[])

    def test_a_language_with_no_runtime_bioaf_can_supply_is_refused_with_the_reason(self):
        with pytest.raises(EnvironmentCheckRefused) as refusal:
            environment_check_request(sources=[{**_PY, "language": "julia"}], manifests=[])
        assert "julia" in str(refusal.value)


class TestWhereItsResultLands:
    def test_a_successful_load_is_recorded_where_the_obligation_reads_it(self):
        evidence = {"code_inspection": {"sources": [_PY]}}
        record_environment_check(
            evidence,
            result={"load": {"status": "succeeded", "environment": "python:3.11.8", "ref": "run-1"}},
        )
        execution = evidence["code_inspection"]["execution"]
        assert execution["load"]["status"] == "succeeded"

    def test_the_obligation_then_verifies_from_that_record_and_not_before(self):
        from app.services.validation_code_checks import assess_code

        assert assess_code(sources=[_PY])["C1.B"]["outcome"] == "undetermined"
        evidence = {"code_inspection": {"sources": [_PY]}}
        record_environment_check(
            evidence,
            result={
                "load": {"status": "succeeded", "environment": "python:3.11.8", "ref": "run-1"},
                "dependency_resolution": {"status": "succeeded", "ref": "run-1"},
            },
        )
        assessed = assess_code(
            sources=[_PY], execution=evidence["code_inspection"]["execution"]
        )
        assert assessed["C1.B"]["outcome"] == "verified"
        assert assessed["C2.B"]["outcome"] == "verified"

    def test_a_failed_load_is_a_named_failure_with_its_reason(self):
        from app.services.validation_code_checks import assess_code

        evidence = {"code_inspection": {"sources": [_PY]}}
        record_environment_check(
            evidence,
            result={"load": {"status": "failed", "reason": "numpy 1.26.4 is not available for this runtime", "ref": "r2"}},
        )
        found = assess_code(sources=[_PY], execution=evidence["code_inspection"]["execution"])["C1.B"]
        assert found["outcome"] == "failed"
        assert "numpy" in found["rationale"]

    def test_an_earlier_review_is_not_discarded_when_a_run_is_recorded(self):
        evidence = {"code_inspection": {"sources": [_PY], "reviews": [{"kind": "fitness", "established": True}]}}
        record_environment_check(evidence, result={"load": {"status": "succeeded", "ref": "r3"}})
        assert evidence["code_inspection"]["reviews"]
