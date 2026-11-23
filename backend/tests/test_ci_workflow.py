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
from datetime import date, timedelta

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


# ── dependency review ──

REVIEW_JOB = "dependency-review"


def _review_step(workflow, uses_prefix: str) -> dict:
    steps = workflow["jobs"][REVIEW_JOB]["steps"]
    return next(s for s in steps if s.get("uses", "").startswith(uses_prefix))


def test_dependency_review_fails_introduced_high_risk_in_every_scope(workflow):
    step = _review_step(workflow, "actions/dependency-review-action@")
    ref = step["uses"].split("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", ref), "pin the action to a reviewed commit"
    inputs = step["with"]
    assert inputs["fail-on-severity"] == "high"
    assert {s.strip() for s in inputs["fail-on-scopes"].split(",")} == {"runtime", "development", "unknown"}
    assert inputs["vulnerability-check"] is True
    assert not step.get("continue-on-error") and "warn-only" not in inputs
    assert inputs["allow-ghsas"] == "${{ steps.policy.outputs.ghsas }}"


def test_dependency_review_reads_the_policy_from_the_base_branch(workflow):
    checkout = _review_step(workflow, "actions/checkout@")
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    run = next(s for s in workflow["jobs"][REVIEW_JOB]["steps"] if s.get("id") == "policy")["run"]
    assert "base/scripts/ci/dependency_report.py" in run and "base/.github/security-exceptions.json" in run


def _run_policy_step(workflow, tmp_path: Path, files: dict[str, str]) -> tuple[int, str]:
    run = next(s for s in workflow["jobs"][REVIEW_JOB]["steps"] if s.get("id") == "policy")["run"]
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    output = tmp_path / "github_output"
    output.write_text("")
    done = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", run],
        cwd=tmp_path,
        env={"GITHUB_OUTPUT": str(output), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        capture_output=True,
        text=True,
    )
    return done.returncode, output.read_text()


def _policy(**entry) -> str:
    base = {
        "ghsa": "GHSA-2222-3333-4444",
        "package": "p",
        "paths": ["p"],
        "versions": "<1",
        "severity": "high",
        "scope": "runtime",
        "rationale": "r",
        "mitigation": "m",
        "owner": "o",
        "approved_by": "a",
        "approval_reference": "ref",
        "approval_date": "2000-01-01",
        "expires": "2000-01-31",
        "allow_pr_introduction": True,
    }
    base.update(entry)
    return json.dumps({"exceptions": [base]})


def test_the_policy_step_allows_nothing_when_the_base_branch_has_no_policy(workflow, tmp_path):
    assert _run_policy_step(workflow, tmp_path, {}) == (0, "ghsas=\n")


def test_the_policy_step_forwards_only_active_approved_entries(workflow, tmp_path):
    script = (REPO / "scripts" / "ci" / "dependency_report.py").read_text()
    today = date.today()
    active = _policy(approval_date=today.isoformat(), expires=(today + timedelta(days=5)).isoformat())
    files = {"base/scripts/ci/dependency_report.py": script, "base/.github/security-exceptions.json": active}
    assert _run_policy_step(workflow, tmp_path, files) == (0, "ghsas=GHSA-2222-3333-4444\n")
    expired = {**files, "base/.github/security-exceptions.json": _policy()}
    assert _run_policy_step(workflow, tmp_path, expired) == (0, "ghsas=\n")


def test_an_invalid_base_policy_fails_the_dependency_review(workflow, tmp_path):
    script = (REPO / "scripts" / "ci" / "dependency_report.py").read_text()
    files = {"base/scripts/ci/dependency_report.py": script, "base/.github/security-exceptions.json": "{broken"}
    code, output = _run_policy_step(workflow, tmp_path, files)
    assert code != 0 and "ghsas=" not in output


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


def test_the_scheduled_audit_is_never_a_pull_request_check():
    """Existing findings fail the daily run and notify; they must not freeze unrelated pull requests."""
    audit = yaml.safe_load((REPO / ".github" / "workflows" / "dependency-audit.yml").read_text())
    triggers = audit[True]  # PyYAML reads the bare key `on` as a boolean
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    run = "\n".join(s.get("run", "") for s in audit["jobs"]["npm-audit"]["steps"])
    assert "scripts/ci/dependency_report.py audit" in run and "--package-lock-only" in run
    assert "|| true" not in run


# ── benchmark dispatch inputs stay data (nov_sec 01A) ──
#
# The worker count is free text typed into a manual dispatch. It reaches the shell only through step environment
# variables, a dedicated step accepts `auto` or 1-4 before any tool is installed, and the always-running summary
# never treats it as code. These tests run the workflow's own scripts with the environment each step declares.

BENCHMARK_INPUTS = {"test_bcrypt_rounds": "4", "durability": "default", "scope": "full", "workers": "auto"}
PAYLOADS = [
    "$(touch {marker})",
    "`touch {marker}`",
    "1'; touch {marker}; echo '",
    '1"; touch {marker}; echo "',
    "1 $(touch {marker})",
]


def _benchmark() -> dict:
    return yaml.safe_load(BENCHMARK.read_text())


def _benchmark_step(name: str) -> tuple[int, dict]:
    steps = _benchmark()["jobs"]["benchmark"]["steps"]
    return next((i, s) for i, s in enumerate(steps) if s.get("name") == name)


def _resolve(value: str, inputs: dict[str, str], outcomes: dict[str, str]) -> str:
    """Evaluate the only expressions a step's env may use here: dispatch inputs and earlier step outcomes."""

    def sub(match: re.Match) -> str:
        expr = match.group(1).strip()
        if m := re.fullmatch(r"inputs\.(\w+)", expr):
            return inputs[m.group(1)]
        if m := re.fullmatch(r"steps\.([\w-]+)\.outcome", expr):
            return outcomes[m.group(1)]
        raise AssertionError(f"unexpected expression in a step env: {expr}")

    return re.sub(r"\$\{\{(.*?)\}\}", sub, str(value))


def _run_benchmark_step(
    name: str,
    tmp_path: Path,
    inputs: dict[str, str] | None = None,
    outcomes: dict[str, str] | None = None,
    pytest_exit: int = 0,
    artifacts: bool = True,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run one benchmark step exactly as written, with the env it declares and the job's explicit bash."""
    _, step = _benchmark_step(name)
    script = step["run"]
    assert "${{" not in script, f"{name} puts a workflow expression in shell source"
    inputs = {**BENCHMARK_INPUTS, **(inputs or {})}
    outcomes = {"validate": "success", **(outcomes or {})}
    bin_dir, work, runner = tmp_path / "bin", tmp_path / "work", tmp_path / "runner"
    for d in (bin_dir, work / "tests", runner):
        d.mkdir(parents=True, exist_ok=True)
    if artifacts:
        (work / "test-artifacts").mkdir(exist_ok=True)
    argv = tmp_path / "pytest-argv"
    (bin_dir / "pytest").write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "{argv}"\necho "1 passed"\nexit {pytest_exit}\n'
    )
    (bin_dir / "nproc").write_text("#!/bin/sh\necho 4\n")
    for tool in ("uv", "python"):
        (bin_dir / tool).write_text("#!/bin/sh\necho stub\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    (work / "uv.lock").write_text("")
    (work / "tests" / "benchmark_subset.txt").write_text("# header\ntests/test_a.py\ntests/test_b.py\n")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "RUNNER_TEMP": str(runner),
        "HOME": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    env.update({k: _resolve(v, inputs, outcomes) for k, v in step.get("env", {}).items()})
    done = subprocess.run([*EXPLICIT_BASH, "-c", script], cwd=work, env=env, capture_output=True, text=True, timeout=60)
    return done, work


def _pytest_argv(tmp_path: Path) -> list[str]:
    return (tmp_path / "pytest-argv").read_text().splitlines()


@pytest.mark.parametrize("workers", ["auto", "1", "2", "4"])
def test_the_benchmark_accepts_auto_and_one_to_four_workers(tmp_path, workers):
    done, _ = _run_benchmark_step("Validate inputs", tmp_path, {"workers": workers})
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize(
    "workers",
    ["", "0", "5", "9", "10", "-1", "+1", " 1", "1 ", "01", "1.0", "0x2", "1e1", "AUTO", "auto ", "four", "9" * 40],
)
def test_the_benchmark_rejects_unsupported_worker_values_without_echoing_them(tmp_path, workers):
    done, _ = _run_benchmark_step("Validate inputs", tmp_path, {"workers": workers})
    assert done.returncode != 0
    # One fixed diagnostic, whatever was typed: the rejected value is never repeated.
    assert (done.stdout + done.stderr).strip() == (
        "::error::Unsupported workers value. Use auto or a whole number from 1 to 4."
    )


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("step", ["Validate inputs", "Run tests", "Summarize"])
def test_a_worker_payload_never_executes_in_any_benchmark_step(tmp_path, payload, step):
    marker = tmp_path / "executed"
    workers = payload.format(marker=marker)
    outcome = "failure" if step == "Summarize" else "success"
    done, _ = _run_benchmark_step(step, tmp_path, {"workers": workers}, {"validate": outcome}, artifacts=False)
    assert not marker.exists(), (step, done.stdout + done.stderr)
    if step == "Validate inputs":
        assert done.returncode != 0


@pytest.mark.parametrize("workers", ["auto", "1", "4"])
def test_accepted_workers_reach_pytest_as_one_exact_argument(tmp_path, workers):
    done, _ = _run_benchmark_step("Run tests", tmp_path, {"workers": workers})
    assert done.returncode == 0, done.stdout + done.stderr
    argv = _pytest_argv(tmp_path)
    assert argv[argv.index("-n") + 1] == workers
    assert argv.count("-n") == 1


@pytest.mark.parametrize(
    ("scope", "targets"), [("subset", ["tests/test_a.py", "tests/test_b.py"]), ("full", ["tests"])]
)
def test_the_benchmark_scope_still_selects_its_tests(tmp_path, scope, targets):
    done, _ = _run_benchmark_step("Run tests", tmp_path, {"scope": scope})
    assert done.returncode == 0, done.stdout + done.stderr
    argv = _pytest_argv(tmp_path)
    for target in targets:
        assert target in argv
    if scope == "subset":
        assert "tests" not in argv and "# header" not in argv


@pytest.mark.parametrize("pytest_exit", [0, 1])
def test_the_benchmark_test_step_keeps_the_pytest_result_through_tee(tmp_path, pytest_exit):
    done, work = _run_benchmark_step("Run tests", tmp_path, {"workers": "2"}, pytest_exit=pytest_exit)
    assert done.returncode == pytest_exit
    assert "1 passed" in (work / "test-artifacts" / "pytest-output.txt").read_text()


def test_the_benchmark_validates_before_installing_anything():
    steps = _benchmark()["jobs"]["benchmark"]["steps"]
    index, validate = _benchmark_step("Validate inputs")
    assert validate.get("id") == "validate"
    assert "if" not in validate and not validate.get("continue-on-error")
    assert steps[0]["uses"].startswith("actions/checkout@") and index == 1
    later = steps[index + 1 :]
    installers = [s for s in later if s.get("uses", "").startswith(("actions/setup-python@", "astral-sh/setup-uv@"))]
    assert len(installers) == 2
    for step in later:
        # A rejected value stops every later step except the evidence steps, which say what was skipped.
        if "always()" in str(step.get("if", "")):
            assert step.get("name") in {"Summarize", "Upload evidence"}, step
    upload = _benchmark_step("Upload evidence")[1]
    assert "steps.validate.outcome == 'success'" in upload["if"]


def test_the_benchmark_summary_survives_a_rejected_value_without_repeating_it(tmp_path):
    marker = tmp_path / "executed"
    workers = f"$(touch {marker})"
    done, _ = _run_benchmark_step("Summarize", tmp_path, {"workers": workers}, {"validate": "failure"}, artifacts=False)
    assert done.returncode == 0, done.stdout + done.stderr
    summary = (tmp_path / "summary.md").read_text()
    assert "touch" not in summary and "rejected" in summary
    assert not marker.exists()


def test_the_benchmark_summary_reports_an_accepted_run(tmp_path):
    done, _ = _run_benchmark_step("Summarize", tmp_path, {"workers": "4", "scope": "subset"})
    assert done.returncode == 0, done.stdout + done.stderr
    summary = (tmp_path / "summary.md").read_text()
    assert "scope=subset" in summary and "workers=4" in summary and "cpus=4" in summary


def test_the_benchmark_keeps_its_dispatch_choices_and_artifact_name():
    inputs = _benchmark()[True]["workflow_dispatch"]["inputs"]
    assert inputs["workers"]["default"] == "auto"
    assert inputs["scope"]["options"] == ["subset", "full"]
    assert inputs["durability"]["options"] == ["default", "tuned"]
    assert inputs["test_bcrypt_rounds"]["options"] == ["4", "12"]
    upload = _benchmark_step("Upload evidence")[1]
    assert upload["with"]["name"] == (
        "benchmark-${{ inputs.scope }}-r${{ inputs.test_bcrypt_rounds }}-${{ inputs.durability }}"
        "-n${{ inputs.workers }}-${{ github.run_id }}"
    )


# ── no workflow expression is shell source (nov_sec 01A) ──

ALL_WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_no_run_script_interpolates_a_workflow_expression(path):
    # GitHub substitutes ${{ }} into the script text before the shell parses it, so any value becomes code.
    # Values enter a step through env and are read as quoted variables.
    workflow = yaml.safe_load(path.read_text())
    offenders = [
        (name, step.get("name") or step.get("uses"))
        for name, job in workflow["jobs"].items()
        for step in job.get("steps", [])
        if "${{" in step.get("run", "")
    ]
    assert offenders == []


# ── the no-changelog label (nov_sec 01A) ──

CHANGELOG = REPO / ".github" / "workflows" / "changelog-check.yml"
LABEL_EXPRESSION = "${{ contains(github.event.pull_request.labels.*.name, 'no-changelog') }}"


def _github_contains(labels: list[str], item: str) -> str:
    """GitHub's contains() over an array: an element equal to the item, compared ignoring case."""
    return "true" if any(label.lower() == item.lower() for label in labels) else "false"


def _run_label_step(tmp_path: Path, labels: list[str]) -> tuple[subprocess.CompletedProcess, str]:
    workflow = yaml.safe_load(CHANGELOG.read_text())
    step = next(s for s in workflow["jobs"]["check"]["steps"] if s.get("id") == "label")
    assert step["env"] == {"HAS_NO_CHANGELOG_LABEL": LABEL_EXPRESSION}
    output = tmp_path / "github_output"
    output.write_text("")
    env = {
        "PATH": "/usr/bin:/bin",
        "GITHUB_OUTPUT": str(output),
        "HAS_NO_CHANGELOG_LABEL": _github_contains(labels, "no-changelog"),
    }
    shell = EXPLICIT_BASH if _effective_shell(workflow, workflow["jobs"]["check"], step) == "bash" else IMPLICIT_BASH
    done = subprocess.run([*shell, "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True, text=True)
    return done, output.read_text()


@pytest.mark.parametrize(
    ("labels", "skip"),
    [
        ([], "false"),
        (["no-changelog"], "true"),
        (["bug", "no-changelog"], "true"),
        (["no-changelog-please"], "false"),
        (["changelog"], "false"),
        (["not no-changelog"], "false"),
    ],
)
def test_the_changelog_check_skips_only_for_the_exact_label(tmp_path, labels, skip):
    done, output = _run_label_step(tmp_path, labels)
    assert done.returncode == 0, done.stdout + done.stderr
    assert output == f"skip={skip}\n"


@pytest.mark.parametrize(
    "label",
    ["$(touch {marker})", "`touch {marker}`", "x' ; touch {marker} ; echo '", 'x" ; touch {marker} ; echo "'],
)
def test_a_label_name_is_never_executed(tmp_path, label):
    marker = tmp_path / "executed"
    done, output = _run_label_step(tmp_path, [label.format(marker=marker)])
    assert not marker.exists()
    assert output == "skip=false\n"


def test_the_changelog_check_keeps_its_policy():
    workflow = yaml.safe_load(CHANGELOG.read_text())
    steps = workflow["jobs"]["check"]["steps"]
    gated = [s for s in steps if s.get("id") != "label"]
    assert gated and all(s.get("if") == "steps.label.outputs.skip != 'true'" for s in gated)
    assert "changes/unreleased/" in gated[-1]["run"]


# ── external actions run reviewed code (nov_sec 01B) ──

USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r"\s+# v\d+\.\d+\.\d+$")


def _uses_lines(path: Path) -> list[tuple[int, str, str]]:
    found = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if m := USES_LINE.match(line):
            found.append((number, m.group("ref").strip("'\""), m.group("rest")))
    return found


def test_the_pin_check_reads_every_action_reference():
    # The raw-line scan and the parsed workflow must agree, or a reference could hide from the pin check.
    for path in ALL_WORKFLOWS:
        parsed = [
            s["uses"]
            for job in yaml.safe_load(path.read_text())["jobs"].values()
            for s in job.get("steps", [])
            if "uses" in s
        ]
        assert sorted(parsed) == sorted(ref for _, ref, _ in _uses_lines(path)), path.name


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_every_external_action_is_pinned_to_a_reviewed_commit(path):
    # A tag can be moved to new code; a full commit SHA cannot. The comment names the release that was reviewed,
    # and Dependabot (github-actions) proposes updates to both together.
    unpinned = [
        (number, ref + rest)
        for number, ref, rest in _uses_lines(path)
        if not ref.startswith(("./", "docker://")) and not (PINNED.fullmatch(ref) and VERSION_COMMENT.fullmatch(rest))
    ]
    assert unpinned == []


@pytest.mark.parametrize(
    ("ref", "rest", "ok"),
    [
        ("actions/checkout@" + "a" * 40, " # v5.1.0", True),
        ("r-lib/actions/setup-r@" + "b" * 40, " # v2.14.0", True),
        ("actions/checkout@v5", "", False),
        ("actions/checkout@v5.1.0", "", False),
        ("actions/checkout@" + "a" * 40, "", False),
        ("actions/checkout@" + "a" * 40, " # v5", False),
        ("actions/checkout@" + "A" * 40, " # v5.1.0", False),
        ("actions/checkout@" + "a" * 39, " # v5.1.0", False),
    ],
)
def test_the_pin_rule_accepts_only_a_full_sha_with_its_release(ref, rest, ok):
    assert bool(PINNED.fullmatch(ref) and VERSION_COMMENT.fullmatch(rest)) is ok


def test_dependabot_proposes_weekly_action_pin_updates():
    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())
    ecosystems = {u["package-ecosystem"]: u for u in config["updates"]}
    assert {"uv", "npm", "github-actions"} <= set(ecosystems)
    actions = ecosystems["github-actions"]
    assert actions["directory"] == "/"
    assert actions["schedule"]["interval"] == "weekly"
    assert 1 <= actions["open-pull-requests-limit"] <= 5
    assert "automerge" not in json.dumps(actions).lower()


# ── checkout credentials do not outlive the checkout (nov_sec 01C) ──

RELEASE = REPO / ".github" / "workflows" / "release.yml"
PUSH_STEP = "Commit, tag, and push"


def _checkouts() -> list[tuple[str, str, dict]]:
    return [
        (path.name, name, step)
        for path in ALL_WORKFLOWS
        for name, job in yaml.safe_load(path.read_text())["jobs"].items()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/checkout@")
    ]


def test_every_checkout_drops_its_credential():
    # No job pushes with, or later fetches through, the checkout's token; the release push authenticates on its own.
    checkouts = _checkouts()
    # 20 before selective CI, plus the classifier's and the workflow-lint job's.
    assert len(checkouts) == 22
    kept = [(f, j) for f, j, s in checkouts if (s.get("with") or {}).get("persist-credentials") is not False]
    assert kept == []
    tokens = [(f, j) for f, j, s in checkouts if "token" in (s.get("with") or {})]
    assert tokens == []


def test_checkouts_keep_their_history_ref_and_sparse_settings():
    by_job = {(f, j): s.get("with") or {} for f, j, s in _checkouts()}
    assert by_job[("ci.yml", "backend-test")]["fetch-depth"] == 0
    assert by_job[("ci.yml", "coverage-comment")]["fetch-depth"] == 0
    assert by_job[("changelog-check.yml", "check")]["fetch-depth"] == 0
    assert by_job[("release.yml", "release")]["fetch-depth"] == 0
    assert by_job[("release.yml", "publish-images")]["ref"] == "${{ needs.release.outputs.tag }}"
    review = by_job[("ci.yml", "dependency-review")]
    assert review["path"] == "base" and review["sparse-checkout-cone-mode"] is False


def _release() -> dict:
    return yaml.safe_load(RELEASE.read_text())


def test_the_release_pat_reaches_only_the_push_step():
    assert RELEASE.read_text().count("secrets.RELEASE_PAT") == 1
    steps = _release()["jobs"]["release"]["steps"]
    holders = [s.get("name") or s.get("uses") for s in steps if "secrets.RELEASE_PAT" in json.dumps(s)]
    assert holders == [PUSH_STEP]
    push = next(s for s in steps if s.get("name") == PUSH_STEP)
    assert push["env"]["RELEASE_PAT"] == "${{ secrets.RELEASE_PAT }}"


def test_the_release_keeps_its_permissions_and_signing():
    jobs = _release()["jobs"]
    assert jobs["release"]["permissions"] == {"contents": "write"}
    assert jobs["publish-images"]["permissions"] == {"contents": "read", "packages": "write"}
    gpg = next(s for s in jobs["release"]["steps"] if s.get("uses", "").startswith("crazy-max/ghaction-import-gpg@"))
    assert gpg["with"]["git_commit_gpgsign"] is True and gpg["with"]["git_tag_gpgsign"] is True
    run = next(s for s in jobs["release"]["steps"] if s.get("name") == PUSH_STEP)["run"]
    assert "git commit -S" in run and "git tag -s" in run


FAKE_GPG = """#!/bin/sh
# Stands in for gpg when git signs: consume the payload, report a created signature, print an armored block.
cat > /dev/null
echo '[GNUPG:] SIG_CREATED D 1 8 00 0 0 0' >&2
printf -- '-----BEGIN PGP SIGNATURE-----\\n\\nZmFrZQ==\\n-----END PGP SIGNATURE-----\\n'
"""
FAKE_PAT = "github_pat_FAKE0000000000000000000000000000000000"


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def _release_checkout(tmp_path: Path, remote: str) -> tuple[Path, dict]:
    """A clone shaped like the release job's workspace after the version bump, with its credential dropped."""
    bin_dir, work, home = tmp_path / "bin", tmp_path / "work", tmp_path / "home"
    for d in (bin_dir, work, home):
        d.mkdir(parents=True)
    (bin_dir / "gpg").write_text(FAKE_GPG)
    (bin_dir / "gpg").chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/usr/bin/false",
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release@example.invalid",
    }
    _git(work, "init", "-q", "-b", "main", env=env)
    _git(work, "config", "user.signingkey", "TESTKEY", env=env)
    for rel in ("backend/pyproject.toml", "backend/uv.lock", "frontend/package.json", "RELEASE_NOTES.md"):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text("v0\n")
    (work / "changes" / "unreleased").mkdir(parents=True)
    (work / "changes" / "unreleased" / "entry.md").write_text("- a change\n")
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-q", "-m", "base", env=env)
    _git(work, "remote", "add", "origin", remote, env=env)
    for rel in ("backend/pyproject.toml", "backend/uv.lock", "frontend/package.json", "RELEASE_NOTES.md"):
        (work / rel).write_text("v1\n")
    (work / "changes" / "unreleased" / "entry.md").unlink()
    return work, env


def _run_push_step(work: Path, env: dict, pat: str) -> subprocess.CompletedProcess:
    step = next(s for s in _release()["jobs"]["release"]["steps"] if s.get("name") == PUSH_STEP)
    assert "${{" not in step["run"]
    step_env = {**env, "TAG": "v2099.1.0", "RELEASE_PAT": pat}
    return subprocess.run(
        [*EXPLICIT_BASH, "-c", step["run"]], cwd=work, env=step_env, capture_output=True, text=True, timeout=60
    )


def _credential_traces(work: Path) -> str:
    config = (work / ".git" / "config").read_text()
    return config + "".join(p.read_text() for p in (work / ".git").glob("credentials*"))


def test_the_release_push_lands_the_signed_commit_and_tag_without_storing_the_pat(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work, env = _release_checkout(tmp_path, str(remote))
    done = _run_push_step(work, env, FAKE_PAT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert FAKE_PAT not in done.stdout + done.stderr
    assert FAKE_PAT not in _credential_traces(work) and "credential" not in _credential_traces(work)
    assert "chore: release v2099.1.0 [skip ci]" in _git(remote, "log", "-1", "--format=%s", "main")
    assert "gpgsig" in _git(remote, "cat-file", "-p", "main")
    tag = _git(remote, "cat-file", "-p", "v2099.1.0")
    assert "Release v2099.1.0" in tag and "BEGIN PGP SIGNATURE" in tag


def test_a_missing_release_pat_fails_the_push_visibly(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work, env = _release_checkout(tmp_path, str(remote))
    done = _run_push_step(work, env, "")
    assert done.returncode != 0
    assert "::error::" in done.stdout + done.stderr and "RELEASE_PAT" in done.stdout + done.stderr
    assert _git(remote, "tag", "--list") == ""
    assert subprocess.run(["git", "rev-parse", "--verify", "-q", "main"], cwd=remote).returncode != 0


class _RecordingRemote:
    """An HTTP git remote that demands Basic credentials, records what it is offered, and refuses everything."""

    def __init__(self):
        import http.server
        import threading

        offered: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                auth = self.headers.get("Authorization")
                if auth:
                    offered.append(auth)
                    self.send_response(403)
                else:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="test"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_GET  # noqa: N815

            def log_message(self, *args):
                pass

        self.offered = offered
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/bioAF/bioAF.git"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_the_release_push_offers_the_pat_only_to_the_push_and_fails_when_it_is_refused(tmp_path):
    import base64

    remote = _RecordingRemote()
    try:
        work, env = _release_checkout(tmp_path, remote.url)
        done = _run_push_step(work, env, FAKE_PAT)
    finally:
        remote.close()
    assert done.returncode != 0
    assert remote.offered, "the push never offered a credential"
    decoded = {base64.b64decode(a.split(" ", 1)[1]).decode() for a in remote.offered}
    assert all(d.endswith(":" + FAKE_PAT) for d in decoded)
    assert FAKE_PAT not in done.stdout + done.stderr
    assert FAKE_PAT not in _credential_traces(work) and "credential" not in _credential_traces(work)


# ── planned skips are granted only explicitly (nov_sec 01D) ──

NEVER_SKIP = "changes,security-scan,dependency-review,docs-lint"


def _gate(required_checks, needs: str, skips: str | None, never: str | None = NEVER_SKIP) -> int:
    argv = ["--needs", needs]
    if skips is not None:
        argv += ["--allowed-skips-json", skips]
    if never is not None:
        argv += ["--never-skip", never]
    return required_checks.main(argv)


def test_a_planned_skip_passes_only_with_explicit_permission(required_checks):
    needs = _needs(changes="success", **{"docs-lint": "success", "backend-test": "skipped"})
    assert _gate(required_checks, needs, '["backend-test"]') == 0
    assert _gate(required_checks, needs, None) == 1
    assert _gate(required_checks, needs, "") == 1
    assert _gate(required_checks, needs, "[]") == 1
    assert _gate(required_checks, needs, '["frontend-test"]') == 1


@pytest.mark.parametrize("result", ["failure", "cancelled", "", "neutral"])
def test_permission_to_skip_never_excuses_another_result(required_checks, result):
    needs = _needs(changes="success", **{"backend-test": result})
    assert _gate(required_checks, needs, '["backend-test"]') == 1


def test_a_permitted_job_that_ran_must_still_succeed(required_checks):
    assert _gate(required_checks, _needs(changes="success", b="success"), '["b"]') == 0
    assert _gate(required_checks, _needs(changes="success", b="failure"), '["b"]') == 1


@pytest.mark.parametrize(
    "skips", ["not json", "{}", '"backend-test"', "[1]", '["backend-test", "backend-test"]', "null", '["unknown-job"]']
)
def test_malformed_permission_fails_the_gate(required_checks, capsys, skips):
    needs = _needs(changes="success", **{"backend-test": "skipped"})
    assert _gate(required_checks, needs, skips) == 1
    assert "::error::" in capsys.readouterr().out


@pytest.mark.parametrize("job", ["changes", "security-scan", "dependency-review", "docs-lint"])
def test_an_always_required_job_cannot_be_permitted_to_skip(required_checks, capsys, job):
    needs = _needs(**{"backend-test": "success", job: "skipped"})
    assert _gate(required_checks, needs, json.dumps([job])) == 1
    assert job in capsys.readouterr().out


# ── ci.yml selects its jobs by change type (nov_sec 01D) ──

CLASSIFIER_JOB = "changes"
LINT_JOB = "workflow-lint"
APPLICATION_JOBS = {
    "terraform-validate",
    "backend-lint",
    "backend-test",
    "frontend-lint",
    "frontend-test",
    "python-sdk",
    "r-sdk",
    "shell-tests",
    "update-agent-systemd",
}
EVERY_PR_JOBS = {"security-scan", "dependency-review", "docs-lint"}
FOCUSED_TESTS = [
    "backend/tests/test_ci_workflow.py",
    "backend/tests/test_ci_coverage_workflow.py",
    "backend/tests/test_dependency_installers.py",
    "backend/tests/test_ci_change_classifier.py",
    "backend/tests/test_workflow_linters.py",
]


def test_ci_runs_on_every_pull_request_and_label_change(workflow):
    trigger = workflow[True]["pull_request"]
    assert trigger["branches"] == ["main"]
    assert trigger["types"] == ["opened", "synchronize", "reopened", "labeled", "unlabeled"]
    assert "paths" not in trigger and "paths-ignore" not in trigger
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert "pull_request_target" not in workflow[True]


def test_the_classifier_reads_the_whole_pull_request(workflow):
    job = workflow["jobs"][CLASSIFIER_JOB]
    assert "if" not in job and "needs" not in job
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@") and checkout["with"]["fetch-depth"] == 0
    [step] = [s for s in job["steps"] if "scripts/ci/classify_changes.py" in s.get("run", "")]
    assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert step["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert '--event "$GITHUB_EVENT_PATH"' in step["run"] and '--output "$GITHUB_OUTPUT"' in step["run"]
    assert job["outputs"] == {
        "mode": f"${{{{ steps.{step['id']}.outputs.mode }}}}",
        "allowed_skips": f"${{{{ steps.{step['id']}.outputs.allowed_skips }}}}",
    }


@pytest.mark.parametrize("job", sorted(APPLICATION_JOBS))
def test_application_jobs_run_only_for_full_validation(workflow, job):
    spec = workflow["jobs"][job]
    assert CLASSIFIER_JOB in spec["needs"]
    assert spec["if"] == "needs.changes.outputs.mode == 'full'"


@pytest.mark.parametrize("job", sorted(EVERY_PR_JOBS))
def test_scanning_review_and_docs_lint_run_on_every_pull_request(workflow, job):
    assert "if" not in workflow["jobs"][job]


def test_the_jobs_are_exactly_the_classified_set(workflow):
    assert set(workflow["jobs"]) == APPLICATION_JOBS | EVERY_PR_JOBS | {CLASSIFIER_JOB, LINT_JOB, GATE_JOB}


def test_the_workflow_lint_job_is_read_only_and_runs_for_ci_and_full_modes(workflow):
    job = workflow["jobs"][LINT_JOB]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["permissions"] == {"contents": "read"}
    assert CLASSIFIER_JOB in job["needs"]
    assert job["if"] == "needs.changes.outputs.mode == 'ci-config' || needs.changes.outputs.mode == 'full'"
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "scripts/ci/install_workflow_linters.py --manifest scripts/ci/workflow_linter_versions.json" in runs
    assert re.search(r"^actionlint\b", runs, re.M)
    assert re.search(r"^zizmor\b", runs, re.M)
    python = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python@"))
    assert python["with"]["python-version"] == "3.12"


def test_the_focused_regressions_run_without_the_backend_environment(workflow):
    [step] = [s for s in workflow["jobs"][LINT_JOB]["steps"] if "--noconftest" in s.get("run", "")]
    run = step["run"]
    assert "-m pip install --require-hashes -r scripts/ci/workflow-tests.lock" in run
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in run
    assert "-c /dev/null --rootdir=. -p no:cacheprovider" in run
    for path in FOCUSED_TESTS:
        assert path in run
        assert (REPO / path).is_file()
    assert "backend/tests " not in run and not run.rstrip().endswith("backend/tests")
    assert "uv sync" not in run


def test_the_focused_lock_holds_only_hashed_test_tooling():
    text = (REPO / "scripts" / "ci" / "workflow-tests.lock").read_text()
    pins = dict(re.findall(r"^([a-z0-9_.-]+)==(\S+)", text, re.M))
    assert set(pins) == {"pytest", "pyyaml", "iniconfig", "packaging", "pluggy", "pygments"}
    blocks = re.split(r"^(?=[a-z])", text, flags=re.M)[1:]
    assert all("--hash=sha256:" in b for b in blocks)


def test_the_gate_needs_the_classifier_and_the_lint_job(workflow):
    needs = set(workflow["jobs"][GATE_JOB]["needs"])
    assert {CLASSIFIER_JOB, LINT_JOB} <= needs


def test_the_gate_takes_skips_only_from_a_successful_classification(workflow):
    step = next(s for s in workflow["jobs"][GATE_JOB]["steps"] if "required_checks.py" in s.get("run", ""))
    assert step["env"]["ALLOWED_SKIPS"] == (
        "${{ needs.changes.result == 'success' && needs.changes.outputs.allowed_skips || '' }}"
    )
    assert '--allowed-skips-json "$ALLOWED_SKIPS"' in step["run"]
    never = re.search(r"--never-skip (\S+)", step["run"]).group(1)
    assert set(never.split(",")) == EVERY_PR_JOBS | {CLASSIFIER_JOB}


def _enforce_coverage(tmp_path: Path, classified: str, mode: str, gate_status: str | None) -> int:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(s for s in workflow["jobs"][GATE_JOB]["steps"] if s.get("name") == "Enforce coverage gates")
    assert step["env"] == {"CLASSIFIED": "${{ needs.changes.result }}", "MODE": "${{ needs.changes.outputs.mode }}"}
    tmp_path.mkdir(parents=True, exist_ok=True)
    if gate_status is not None:
        (tmp_path / "gate-status").write_text(gate_status + "\n")
    env = {"PATH": "/usr/bin:/bin", "CLASSIFIED": classified, "MODE": mode}
    return subprocess.run([*EXPLICIT_BASH, "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True).returncode


@pytest.mark.parametrize(
    ("classified", "mode", "gate_status", "code"),
    [
        ("success", "full", "0", 0),
        ("success", "full", "1", 1),
        ("success", "full", None, 1),
        ("success", "docs", None, 0),
        ("success", "ci-config", None, 0),
        ("failure", "docs", None, 1),
        ("cancelled", "ci-config", None, 1),
        ("success", "", None, 1),
        ("success", "weird", None, 1),
        ("success", "docs ", None, 1),
        ("", "", None, 1),
    ],
)
def test_coverage_gates_apply_unless_a_cheaper_mode_was_classified(tmp_path, classified, mode, gate_status, code):
    assert _enforce_coverage(tmp_path / "run", classified, mode, gate_status) == code


def test_cheaper_modes_skip_the_coverage_artifacts_and_report(workflow):
    steps = workflow["jobs"][GATE_JOB]["steps"]
    full_only = "needs.changes.result != 'success' || needs.changes.outputs.mode == 'full'"
    downloads = [s for s in steps if s.get("uses", "").startswith("actions/download-artifact@")]
    assert len(downloads) == 2 and all(s["if"] == full_only for s in downloads)
    build = next(s for s in steps if s.get("name") == "Build report")
    assert build["if"] == full_only
    report = next(s for s in steps if "classify_changes.py report" in s.get("run", ""))
    assert report["if"] == "needs.changes.result == 'success' && needs.changes.outputs.mode != 'full'"


def test_the_build_workflow_keeps_its_own_path_filters():
    build = yaml.safe_load((REPO / ".github" / "workflows" / "build.yml").read_text())
    assert build[True]["pull_request"]["paths"] == [
        "backend/**",
        "frontend/**",
        "docker/Dockerfile*",
        "docker/docker-compose*",
        "scripts/ci/check_image_contents.py",
        "scripts/ci/frontend_image_smoke.sh",
        "docker/nginx.conf",
    ]


def test_a_rejected_release_push_updates_neither_main_nor_the_tag(tmp_path):
    # A merge lands on main while the release job runs: the push of HEAD:main is not a fast-forward. The commit and
    # the tag must be refused together, or the tag would name a release commit that never reached main.
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work, env = _release_checkout(tmp_path, str(remote))
    _git(work, "push", "-q", "origin", "HEAD:main", env=env)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other), env=env)
    (other / "concurrent.txt").write_text("merged meanwhile\n")
    _git(other, "add", "concurrent.txt", env=env)
    _git(other, "commit", "-q", "-m", "concurrent merge", env=env)
    _git(other, "push", "-q", "origin", "HEAD:main", env=env)
    concurrent = _git(remote, "rev-parse", "main").strip()

    done = _run_push_step(work, env, FAKE_PAT)
    assert done.returncode != 0
    assert _git(remote, "rev-parse", "main").strip() == concurrent
    assert _git(remote, "tag", "--list") == ""


def test_the_secret_scanner_image_is_pinned_by_digest(workflow):
    # The trufflehog action is a wrapper that runs ghcr.io/trufflesecurity/trufflehog:<version>, `latest` by default.
    # Pinning the wrapper does not pin the scanner; the version input names a release and its image digest.
    step = next(
        s for s in workflow["jobs"]["security-scan"]["steps"] if "trufflesecurity/trufflehog@" in s.get("uses", "")
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", step["with"]["version"])
    assert step["with"]["extra_args"] == "--only-verified"
