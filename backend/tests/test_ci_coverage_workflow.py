"""The CI workflow measures coverage the way plan 8 agreed and enforces the gates.

Workflow wiring is otherwise only proven by running CI; these checks pin the contract a pull request
cannot see from its own diff: the comprehensive backend run is not fail-fast, the reviewed unit
selection is measured on its own without a database, and the report is run with the policy enforced.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _steps(job: str) -> dict[str, dict]:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    return {step.get("name", ""): step for step in jobs[job]["steps"]}


def test_the_comprehensive_backend_run_is_not_fail_fast():
    steps = _steps("backend-test")
    run = steps["Run tests"]["run"]
    assert "pytest" in run and "--cov=app" in run
    assert not re.search(r"(^|\s)(-x|--exitfirst|--maxfail)(\s|=|$)", run), run
    recorded = steps["Record test environment"]["run"]
    assert not re.search(r"pytest -x\b", recorded), "the recorded command still says -x"


def test_the_unit_selection_is_measured_separately_without_a_database():
    step = _steps("backend-test")["Run the unit selection"]
    assert step["if"] == "always()"
    assert "python -m tests.support.unit_selection" in step["run"]
    assert "unit-coverage.json" in step["run"] and "unit-exit-status" in step["run"]
    env = step["env"]
    assert env["COVERAGE_FILE"] != ".coverage", "unit data must not land where the combined run combines"
    assert ":5432/" not in env["BIOAF_TEST_DATABASE_URL"], "the unit step must not see the service database"


def test_the_report_enforces_the_policy_with_the_unit_result():
    steps = _steps("coverage-comment")
    build = steps["Build report"]["run"]
    for flag in (
        "--policy scripts/ci/coverage_policy.json",
        "--enforce",
        "--backend-unit coverage-in/backend/unit-coverage.json",
        "--backend-unit-status",
    ):
        assert flag in build, flag
    enforce = steps["Enforce coverage gates"]
    assert enforce["if"] == "always()"
