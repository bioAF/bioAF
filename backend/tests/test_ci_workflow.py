"""The PR workflow's merge gate and toolchain contracts (.github/workflows/ci.yml, scripts/ci/required_checks.py).

The ruleset on main requires one check, Coverage Report. It is the merge gate only if it depends on every
other job in the workflow and fails unless each of them succeeded: a failed, cancelled or skipped job must
not pass for a successful one.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
SCRIPT = REPO / "scripts" / "ci" / "required_checks.py"
GATE_JOB = "coverage-comment"


@pytest.fixture(scope="module")
def required_checks():
    spec = importlib.util.spec_from_file_location("required_checks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _needs(**results: str) -> str:
    return json.dumps({job: {"result": result, "outputs": {}} for job, result in results.items()})


# ── required_checks.py ──


def test_every_dependency_succeeding_passes(required_checks, capsys):
    assert required_checks.main(["--needs", _needs(a="success", b="success")]) == 0
    assert "a: success" in capsys.readouterr().out


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "", "neutral"])
def test_any_other_result_fails_and_names_the_job(required_checks, capsys, result):
    assert required_checks.main(["--needs", _needs(a="success", b=result)]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "b" in out.split("::error::", 1)[1]


def test_no_dependencies_fails(required_checks):
    assert required_checks.main(["--needs", "{}"]) == 1


@pytest.mark.parametrize("raw", ["", "null", "not json", "[]"])
def test_unreadable_needs_fails(required_checks, raw):
    assert required_checks.main(["--needs", raw]) == 1


def test_a_dependency_without_a_result_fails(required_checks):
    assert required_checks.main(["--needs", json.dumps({"a": {"outputs": {}}})]) == 1


def test_summary_is_written_when_asked(required_checks, tmp_path):
    summary = tmp_path / "summary.md"
    required_checks.main(["--needs", _needs(a="success", b="failure"), "--summary", str(summary)])
    text = summary.read_text()
    assert "| a | success |" in text
    assert "| b | failure |" in text


# ── ci.yml wiring ──


def test_the_gate_keeps_its_required_check_name(workflow):
    assert workflow["jobs"][GATE_JOB]["name"] == "Coverage Report"


def test_the_gate_depends_on_every_other_job(workflow):
    needs = workflow["jobs"][GATE_JOB]["needs"]
    others = set(workflow["jobs"]) - {GATE_JOB}
    assert set(needs) == others


def test_the_gate_runs_whatever_its_dependencies_did(workflow):
    assert workflow["jobs"][GATE_JOB]["if"].startswith("always()")


def test_the_gate_enforces_every_dependency_result_in_an_always_step(workflow):
    steps = workflow["jobs"][GATE_JOB]["steps"]
    enforcing = [s for s in steps if "scripts/ci/required_checks.py" in s.get("run", "")]
    assert len(enforcing) == 1
    step = enforcing[0]
    assert step["if"] == "always()"
    assert "toJSON(needs)" in json.dumps(step.get("env", {}))
    assert not step.get("continue-on-error")


def test_ci_validates_terraform_with_the_version_the_backend_image_ships(workflow):
    dockerfile = (REPO / "docker" / "Dockerfile.backend").read_text()
    shipped = re.search(r"releases\.hashicorp\.com/terraform/(\d+\.\d+\.\d+)/", dockerfile)
    assert shipped
    versions = {
        step["with"]["terraform_version"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("hashicorp/setup-terraform")
    }
    assert versions == {shipped.group(1)}


def test_every_run_step_fails_on_a_failure_anywhere_in_a_pipeline(workflow):
    # GitHub's implicit shell is `bash -e {0}`, without pipefail: `pytest | tee out || status=$?` would
    # record tee's success and pass a failed run. An explicit `bash` is `bash --noprofile --norc -eo
    # pipefail {0}`.
    assert workflow["defaults"]["run"]["shell"] == "bash"
    for name, job in workflow["jobs"].items():
        assert job.get("defaults", {}).get("run", {}).get("shell", "bash") == "bash", name
        for step in job.get("steps", []):
            assert step.get("shell", "bash") == "bash", (name, step.get("name"))
