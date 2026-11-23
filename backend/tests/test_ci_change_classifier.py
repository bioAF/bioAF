"""Pull request change classification (scripts/ci/classify_changes.py).

CI runs on every pull request and selects its jobs by what the whole pull request changes: documentation only,
CI configuration only, or anything else (full validation). Unknown paths, workflow files, the classifier itself and
any failure select or fail toward full validation; the exact `ci:full` label forces it.

Standard library and PyYAML only: these tests run in the focused workflow job without the backend environment.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "classify_changes.py"

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
ALWAYS_REQUIRED = {"changes", "security-scan", "dependency-review", "docs-lint"}


@pytest.fixture(scope="module")
def classifier():
    spec = importlib.util.spec_from_file_location("classify_changes", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── one path ──

DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
    "GEMINI.md",
    "RELEASE_NOTES.md",
    "docs/index.md",
    "docs/a/b/deep.md",
    "decisions/ADR-001.md",
    "decisions/sub/ADR-002.md",
    "ai_guides/README.md",
    "ai_guides/tdd.md",
    "changes/unreleased/README.md",
    "changes/unreleased/bmills-something.md",
    ".github/pull_request_template.md",
    "assets/bioAF_Architecture.svg",
    "assets/bioAF_Architecture.png",
]
CI_CONFIG = [".github/dependabot.yml", ".markdownlint.json"]
FULL = [
    # Workflows, whatever changed in them (comments, action pins, setup-uv/setup-node updates).
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/new.yaml",
    # Configuration and policy next to the allowlisted files.
    ".github/security-exceptions.json",
    ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/bug.md",
    # Code, tests, fixtures, SDKs, migrations, scripts, contracts, dependencies, images, infrastructure.
    "backend/app/main.py",
    "backend/tests/test_x.py",
    "backend/tests/fixtures/multiqc/README.md",
    "backend/alembic/versions/001_x.py",
    "backend/contracts/internal-openapi.json",
    "backend/contracts/README.md",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/DEPENDENCIES.md",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/.eslintrc.json",
    "frontend/tsconfig.json",
    "frontend/public/logo.png",
    "frontend/DEPENDENCIES.md",
    "sdk/python/README.md",
    "sdk/r/DESCRIPTION",
    "scripts/ci/classify_changes.py",
    "scripts/ci/workflow_linter_versions.json",
    "scripts/ci/workflow-tests.lock",
    "scripts/ci/README.md",
    "docker/Dockerfile.backend",
    "backend/terraform/modules/x/main.tf",
    "bioaf",
    "install.sh",
    # Near misses of the documentation allowlist.
    "readme.md",
    "README.MD",
    "docs/notes.txt",
    "docs/image.png",
    "docs",
    "ai_guides/README.md.bak",
    "changes/unreleased/nested/entry.md",
    "changes/unreleased/entry.txt",
    "changes/README.md",
    "assets/bioAF-logo.svg",
    "assets/bioAF_Architecture.jpg",
    "sub/README.md",
    "subdocs/x.md",
    "x/docs/y.md",
    ".markdownlint-cli2.yaml",
    "dependabot.yml",
    "unknown-new-top-level-file",
]


@pytest.mark.parametrize("path", DOCS)
def test_documentation_paths_are_documentation(classifier, path):
    assert classifier.classify_path(path) == "docs"


@pytest.mark.parametrize("path", CI_CONFIG)
def test_ci_configuration_paths_are_ci_configuration(classifier, path):
    assert classifier.classify_path(path) == "ci-config"


@pytest.mark.parametrize("path", FULL)
def test_every_other_path_selects_full_validation(classifier, path):
    assert classifier.classify_path(path) == "full"


# ── a whole pull request ──


@pytest.mark.parametrize(
    ("paths", "mode"),
    [
        (["README.md"], "docs"),
        (["docs/a.md", "ai_guides/tdd.md", "assets/bioAF_Architecture.png"], "docs"),
        ([".github/dependabot.yml"], "ci-config"),
        ([".markdownlint.json", "README.md"], "ci-config"),
        (["backend/app/x.py"], "full"),
        (["frontend/src/x.tsx"], "full"),
        (["README.md", "backend/app/x.py"], "full"),
        ([".github/dependabot.yml", ".github/workflows/ci.yml"], "full"),
        (["README.md", "something-new"], "full"),
        ([], "full"),
    ],
)
def test_a_pull_request_takes_its_strongest_mode(classifier, paths, mode):
    assert classifier.classify(paths, labels=[]).mode == mode


def test_the_reasons_name_what_forced_the_mode(classifier):
    result = classifier.classify(["README.md", "backend/app/x.py", "zzz"], labels=[])
    assert result.mode == "full"
    assert any("backend/app/x.py" in r for r in result.reasons)
    assert any("zzz" in r for r in result.reasons)
    assert classifier.classify([], labels=[]).reasons


@pytest.mark.parametrize("paths", [["README.md"], [".github/dependabot.yml"], ["backend/app/x.py"]])
def test_the_ci_full_label_forces_full_validation(classifier, paths):
    result = classifier.classify(paths, labels=["bug", "ci:full"])
    assert result.mode == "full"
    assert any("ci:full" in r for r in result.reasons)


@pytest.mark.parametrize("label", ["CI:FULL", "ci:full ", "ci-full", "ci:fuller", "no-changelog", "$(touch x)"])
def test_other_labels_leave_classification_alone(classifier, label):
    assert classifier.classify(["README.md"], labels=[label]).mode == "docs"


def test_each_mode_skips_only_application_and_lint_jobs(classifier):
    assert set(classifier.allowed_skips("full")) == set()
    assert set(classifier.allowed_skips("ci-config")) == APPLICATION_JOBS
    assert set(classifier.allowed_skips("docs")) == APPLICATION_JOBS | {"workflow-lint"}
    for mode in ("full", "ci-config", "docs"):
        assert not set(classifier.allowed_skips(mode)) & ALWAYS_REQUIRED


# ── the command, against real git history ──


def _git(cwd: Path, *args: str) -> str:
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, changes: dict[str, str | None], message: str) -> str:
    for rel, text in changes.items():
        if text is None:
            _git(repo, "rm", "-q", rel)
        else:
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
            _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"README.md": "a\n", "backend/app/x.py": "x = 1\n", "docs/old.md": "o\n"}, "base")
    return repo


def _run(repo: Path, base: str, head: str, labels: list[str] | None = None, event: bool = True):
    out, summary, event_path = repo.parent / "out", repo.parent / "summary.md", repo.parent / "event.json"
    out.write_text("")
    if event:
        event_path.write_text(json.dumps({"pull_request": {"labels": [{"name": n} for n in (labels or [])]}}))
    done = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--base",
            base,
            "--head",
            head,
            "--event",
            str(event_path) if event else str(repo.parent / "missing.json"),
            "--output",
            str(out),
            "--summary",
            str(summary),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GITHUB_SHA": "merge-sha"},
    )
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return done, outputs, summary.read_text() if summary.exists() else ""


def test_the_command_classifies_every_commit_of_the_pull_request(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "pr")
    _commit(repo, {"backend/app/x.py": "x = 2\n"}, "code first")
    head = _commit(repo, {"README.md": "b\n"}, "docs last")
    done, outputs, summary = _run(repo, base, head)
    assert done.returncode == 0, done.stderr
    assert outputs["mode"] == "full"
    assert json.loads(outputs["allowed_skips"]) == []
    assert "backend/app/x.py" in summary and "full" in summary


def test_the_command_measures_from_the_merge_base(repo):
    _git(repo, "switch", "-q", "-c", "pr")
    head = _commit(repo, {"docs/new.md": "n\n"}, "docs")
    _git(repo, "switch", "-q", "main")
    moved_base = _commit(repo, {"backend/app/x.py": "x = 3\n"}, "main moved on")
    done, outputs, _ = _run(repo, moved_base, head)
    assert done.returncode == 0, done.stderr
    assert outputs["mode"] == "docs", "main's own later code change must not count against the pull request"
    assert set(json.loads(outputs["allowed_skips"])) == APPLICATION_JOBS | {"workflow-lint"}


@pytest.mark.parametrize(
    ("change", "mode"),
    [
        ({"docs/old.md": None}, "docs"),
        ({"backend/app/x.py": None}, "full"),
    ],
)
def test_deleted_paths_are_classified(repo, change, mode):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, change, "delete")
    assert _run(repo, base, head)[1]["mode"] == mode


def test_a_rename_counts_both_its_old_and_new_path(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "backend/app/x.py", "docs/x.md")
    head = _commit(repo, {}, "rename code into docs")
    done, outputs, summary = _run(repo, base, head)
    assert outputs["mode"] == "full"
    assert "backend/app/x.py" in summary


def test_the_label_forces_full_through_the_event(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, {"README.md": "c\n"}, "docs")
    assert _run(repo, base, head)[1]["mode"] == "docs"
    done, outputs, summary = _run(repo, base, head, labels=["ci:full"])
    assert outputs["mode"] == "full" and "ci:full" in summary


@pytest.mark.parametrize("bad", ["0" * 40, "not-a-ref"])
def test_an_unavailable_diff_fails_without_selecting_a_mode(repo, bad):
    head = _git(repo, "rev-parse", "HEAD")
    done, outputs, _ = _run(repo, bad, head)
    assert done.returncode != 0
    assert "mode" not in outputs and "allowed_skips" not in outputs
    assert "::error::" in done.stdout + done.stderr


def test_an_unreadable_event_fails_without_selecting_a_mode(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, {"README.md": "d\n"}, "docs")
    done, outputs, _ = _run(repo, base, head, event=False)
    assert done.returncode != 0 and "mode" not in outputs


def test_the_summary_records_the_commits_and_mode(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, {"README.md": "e\n"}, "docs")
    _, _, summary = _run(repo, base, head)
    assert head in summary and "merge-sha" in summary and "docs" in summary


def test_a_label_name_is_data_in_the_summary(repo):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, {"README.md": "f\n"}, "docs")
    marker = repo.parent / "executed"
    done, outputs, _ = _run(repo, base, head, labels=[f"$(touch {marker})", "`touch x`"])
    assert done.returncode == 0 and outputs["mode"] == "docs" and not marker.exists()


# ── the report for a cheaper mode ──


def test_a_cheaper_mode_reports_that_tests_were_not_required(classifier):
    needs = {
        "changes": {"result": "success"},
        "docs-lint": {"result": "success"},
        "backend-test": {"result": "skipped"},
    }
    text = classifier.mode_report("docs", json.dumps(needs))
    assert text.startswith("<!-- bioaf-coverage-report -->")
    assert "not required" in text and "docs" in text
    assert "| docs-lint | success |" in text and "| backend-test | skipped |" in text
