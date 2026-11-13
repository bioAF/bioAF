"""The PR workflow's merge gate and toolchain contracts (.github/workflows/ci.yml, scripts/ci/required_checks.py).

The ruleset on main requires one check, Coverage Report. It is the merge gate only if it depends on every
other job in the workflow and fails unless each of them succeeded: a failed, cancelled or skipped job must
not pass for a successful one.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
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


# GitHub's implicit shell is `bash -e {0}`, without pipefail: `pytest | tee out || status=$?` records tee's
# success and passes a failed run. An explicit `bash` is `bash --noprofile --norc -eo pipefail {0}`.
IMPLICIT_BASH = ["bash", "-e"]
EXPLICIT_BASH = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]
BENCHMARK = REPO / ".github" / "workflows" / "backend-benchmark.yml"


def _effective_shell(workflow: dict, job: dict, step: dict) -> str | None:
    """The shell GitHub runs a step with. A job's own ``defaults.run`` replaces the workflow's, so a job that
    sets only its working directory gets the implicit shell (seen in the runner log as ``bash -e {0}``)."""
    if "shell" in step:
        return step["shell"]
    if "run" in job.get("defaults", {}):
        return job["defaults"]["run"].get("shell")
    return workflow.get("defaults", {}).get("run", {}).get("shell")


def test_effective_shell_follows_job_defaults_replacing_workflow_defaults():
    workflow = {"defaults": {"run": {"shell": "bash"}}}
    assert _effective_shell(workflow, {}, {}) == "bash"
    assert _effective_shell(workflow, {"defaults": {"run": {"working-directory": "backend"}}}, {}) is None
    assert _effective_shell(workflow, {"defaults": {"run": {"working-directory": "b", "shell": "bash"}}}, {}) == "bash"
    assert _effective_shell({}, {}, {"shell": "bash"}) == "bash"


def test_every_run_step_fails_on_a_failure_anywhere_in_a_pipeline(workflow):
    implicit = [
        (name, step.get("name"))
        for name, job in workflow["jobs"].items()
        for step in job.get("steps", [])
        if "run" in step and _effective_shell(workflow, job, step) != "bash"
    ]
    assert implicit == []


def _tee_steps(workflow: dict) -> list[tuple[str, dict, dict]]:
    """Backend test steps: pytest from PATH piped into tee, keeping the pipeline's status. (The SDK and bats
    steps share the shape but need their own fixtures to run; the structural guard above covers their shell.)"""
    return [
        (name, job, step)
        for name, job in workflow["jobs"].items()
        for step in job.get("steps", [])
        if re.search(r"^\s*pytest ", step.get("run", ""), re.M)
        and re.search(r"\| tee [^\n]*\|\| status=\$\?", step.get("run", ""))
    ]


def _run_step(script: str, shell: list[str], tmp_path: Path, pytest_exit: int) -> tuple[int, Path]:
    """Run a step script with every external tool it calls stubbed; pytest prints a failure and exits as told."""
    bin_dir, work = tmp_path / "bin", tmp_path / "work"
    for d in (bin_dir, work / "test-artifacts", tmp_path / "runner"):
        d.mkdir(parents=True, exist_ok=True)
    (bin_dir / "pytest").write_text(f"#!/bin/sh\necho '1 failed, 7888 passed'\nexit {pytest_exit}\n")
    for tool in ("uv", "python"):
        (bin_dir / tool).write_text("#!/bin/sh\necho stub\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    (work / "uv.lock").write_text("")
    # Workflow expressions are not shell; give each one a plain value (a full-suite benchmark, one worker).
    script = re.sub(r"\$\{\{\s*inputs\.scope\s*\}\}", "full", script)
    script = re.sub(r"\$\{\{[^}]*\}\}", "1", script)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path / "runner"), "HOME": str(tmp_path)}
    result = subprocess.run([*shell, "-c", script], cwd=work, env=env, capture_output=True, text=True, timeout=60)
    return result.returncode, work


def test_the_implicit_shell_really_masks_a_failed_pipeline(tmp_path):
    # The premise of the two tests below: without pipefail the step passes a failed run.
    script = "status=0\npytest 2>&1 | tee out.txt || status=$?\nexit $status\n"
    assert _run_step(script, IMPLICIT_BASH, tmp_path / "implicit", pytest_exit=1)[0] == 0
    assert _run_step(script, EXPLICIT_BASH, tmp_path / "explicit", pytest_exit=1)[0] == 1


@pytest.mark.parametrize("path", [WORKFLOW, BENCHMARK], ids=lambda p: p.name)
def test_a_failed_test_run_fails_its_step_and_records_the_failure(path, tmp_path):
    workflow = yaml.safe_load(path.read_text())
    steps = _tee_steps(workflow)
    assert steps, f"no test step pipes through tee in {path.name}"
    for name, job, step in steps:
        shell = EXPLICIT_BASH if _effective_shell(workflow, job, step) == "bash" else IMPLICIT_BASH
        failed, work = _run_step(step["run"], shell, tmp_path / f"{name}-failed", pytest_exit=1)
        assert failed == 1, (name, step.get("name"))
        status_file = work / "test-artifacts" / "exit-status"
        if "test-artifacts/exit-status" in step["run"]:
            assert status_file.read_text().strip() == "1", (name, step.get("name"))
        passed, _ = _run_step(step["run"], shell, tmp_path / f"{name}-passed", pytest_exit=0)
        assert passed == 0, (name, step.get("name"))


def test_the_shell_job_gates_its_bats_output(workflow):
    # Bats exits 0 when it finds no tests; bats_status.py fails a run that executed none.
    steps = workflow["jobs"]["shell-tests"]["steps"]
    gating = [s for s in steps if "scripts/ci/bats_status.py" in s.get("run", "")]
    assert len(gating) == 1
    assert "bats-output.tap" in gating[0]["run"]
    assert not gating[0].get("continue-on-error")
