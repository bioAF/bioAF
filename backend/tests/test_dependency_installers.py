"""Every backend dependency installer consumes backend/uv.lock strictly (plan 11).

CI, the benchmark workflow, the release workflow and the backend image install with `uv sync --locked`, which fails
when the lock is stale instead of rewriting it, and all use one selected uv version. The image puts its locked
environment first on PATH, so bare `python`, `alembic` and `uvicorn` (the released ./bioaf's `docker compose exec`
calls and the development Compose command) resolve to it without activation or a uv wrapper.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
DOCKERFILE = REPO / "docker" / "Dockerfile.backend"


@cache
def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _steps(workflow: str, job: str) -> list[dict]:
    return _workflow(workflow)["jobs"][job]["steps"]


def _runs(workflow: str, job: str) -> str:
    return "\n".join(s.get("run", "") for s in _steps(workflow, job))


def _dockerfile_instructions() -> list[str]:
    text = DOCKERFILE.read_text().replace("\\\n", " ")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def _image_uv_version() -> str:
    match = re.search(r"^COPY --from=ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+) ", DOCKERFILE.read_text(), re.MULTILINE)
    assert match, "the backend image copies uv from a versioned uv image"
    return match.group(1)


BACKEND_INSTALLERS = [
    ("ci.yml", "backend-lint"),
    ("ci.yml", "backend-test"),
    ("backend-benchmark.yml", "benchmark"),
]


def _setup_uv(workflow: str, job: str) -> dict:
    [step] = [s for s in _steps(workflow, job) if s.get("uses", "").startswith("astral-sh/setup-uv@")]
    return step


# ── one uv version ──


@pytest.mark.parametrize(("workflow", "job"), BACKEND_INSTALLERS + [("release.yml", "release")])
def test_workflows_use_the_image_uv_version(workflow, job):
    assert _setup_uv(workflow, job)["with"]["version"] == _image_uv_version()


# ── CI and benchmarks ──


@pytest.mark.parametrize(("workflow", "job"), BACKEND_INSTALLERS)
def test_backend_jobs_install_the_locked_development_environment(workflow, job):
    runs = _runs(workflow, job)
    assert re.search(r"^\s*uv sync --locked\b(?!.*--no-dev)", runs, re.MULTILINE)
    assert "pip install" not in runs
    assert "requirements" not in runs


@pytest.mark.parametrize(("workflow", "job"), BACKEND_INSTALLERS)
def test_backend_job_caches_are_keyed_on_the_lock(workflow, job):
    setup = _setup_uv(workflow, job)["with"]
    assert setup.get("enable-cache") is True
    assert setup["cache-dependency-glob"] == "backend/uv.lock"
    for step in _steps(workflow, job):
        assert not step.get("uses", "").startswith("actions/cache"), "the pip cache keyed on requirements is retired"


def test_lint_checks_the_lock_matches_the_project_metadata():
    # Inside the required gate: backend-lint is one of Coverage Report's needs (test_ci_workflow.py).
    assert re.search(r"^\s*uv lock --check\b", _runs("ci.yml", "backend-lint"), re.MULTILINE)
    assert "backend-lint" in _workflow("ci.yml")["jobs"]["coverage-comment"]["needs"]


@pytest.mark.parametrize(("workflow", "job"), [("ci.yml", "backend-test"), ("backend-benchmark.yml", "benchmark")])
def test_evidence_records_installed_versions_lock_identity_and_uv(workflow, job):
    runs = _runs(workflow, job)
    assert "uv pip freeze > test-artifacts/pip-freeze.txt" in runs
    assert "uv --version" in runs
    assert re.search(r"sha256sum uv\.lock", runs)


# ── the backend image ──


def test_the_image_installs_the_locked_runtime_environment_before_copying_the_source():
    instructions = _dockerfile_instructions()
    sync = next(i for i, line in enumerate(instructions) if line.startswith("RUN") and "uv sync" in line)
    assert re.search(r"uv sync --locked --no-dev\b", instructions[sync])
    copies = [line for line in instructions[:sync] if line.startswith("COPY ")]
    assert any("pyproject.toml" in c and "uv.lock" in c for c in copies)
    later = "\n".join(instructions[sync:])
    assert "COPY app/ app/" in later
    assert "requirements" not in DOCKERFILE.read_text()


def test_the_image_puts_the_locked_environment_first_on_path():
    env = [line for line in _dockerfile_instructions() if line.startswith("ENV ")]
    assert any(re.fullmatch(r'ENV PATH="/app/\.venv/bin:\$PATH"', line) for line in env)


def test_the_image_keeps_bare_uvicorn_as_its_command():
    # backend/entrypoint.sh runs the startup database upgrade only when its first argument is uvicorn.
    [cmd] = [line for line in _dockerfile_instructions() if line.startswith("CMD ")]
    assert cmd.startswith('CMD ["uvicorn", ')


# ── release ──


def test_release_refreshes_and_checks_the_lock_after_the_version_bump():
    steps = _steps("release.yml", "release")
    names = [s.get("name", "") for s in steps]
    bump = names.index("Update version in source files")
    commit = names.index("Commit, tag, and push")
    refresh = [i for i, s in enumerate(steps) if re.search(r"^\s*uv lock\b(?! --check)", s.get("run", ""), re.M)]
    assert refresh and bump <= refresh[0] < commit
    run = steps[refresh[0]]["run"]
    assert "--upgrade" not in run
    assert re.search(r"^\s*uv lock --check\b", run, re.MULTILINE)


def test_release_commits_the_lock_with_the_version():
    run = next(s["run"] for s in _steps("release.yml", "release") if s.get("name") == "Commit, tag, and push")
    add = next(line for line in run.splitlines() if line.strip().startswith("git add "))
    assert "backend/pyproject.toml" in add and "backend/uv.lock" in add


# ── documentation and stragglers ──


def test_nothing_installs_from_the_retired_requirements_lists():
    paths = [REPO / "README.md", DOCKERFILE, *WORKFLOWS.glob("*.yml"), REPO / "bioaf", REPO / "install.sh"]
    for path in paths:
        text = path.read_text()
        assert "requirements.txt" not in text and "requirements-dev.txt" not in text, path


def test_the_readme_documents_the_locked_development_setup():
    assert "uv sync --locked" in (REPO / "README.md").read_text()
