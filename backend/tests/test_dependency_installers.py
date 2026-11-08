"""Every backend dependency installer consumes backend/uv.lock strictly (plan 11).

CI, the benchmark workflow, the release workflow and the backend image install with `uv sync --locked`, which fails
when the lock is stale instead of rewriting it, and all use one selected uv version. The image puts its locked
environment first on PATH, so bare `python`, `alembic` and `uvicorn` (the released ./bioaf's `docker compose exec`
calls and the development Compose command) resolve to it without activation or a uv wrapper.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
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


@pytest.mark.parametrize(("workflow", "job"), BACKEND_INSTALLERS + [("release.yml", "release")])
def test_setup_uv_is_referenced_by_a_full_release_tag(workflow, job):
    # astral-sh/setup-uv publishes no moving major tag: `@v10` fails at job setup ("unable to find version").
    assert re.fullmatch(r"astral-sh/setup-uv@v\d+\.\d+\.\d+", _setup_uv(workflow, job)["uses"])


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


# ── release preparation, run for real against a copy ──


def _release_step(name: str) -> str:
    return next(s["run"] for s in _steps("release.yml", "release") if s.get("name") == name)


def _selections(lock: Path) -> set[tuple[str, str]]:
    packages = tomllib.loads(lock.read_text())["package"]
    return {(p["name"], p["version"]) for p in packages if p["name"] != "bioaf-backend"}


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed (CI installs it with setup-uv)")
def test_a_version_only_release_refreshes_the_lock_without_changing_selections(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy(REPO / "backend" / name, backend / name)
    bump = next(line for line in _release_step("Update version in source files").splitlines() if "sed -i" in line)
    env = {**os.environ, "VERSION": "2099.1.0"}
    subprocess.run(["bash", "-c", bump.strip()], cwd=tmp_path, env=env, check=True)

    stale = subprocess.run(["uv", "lock", "--check"], cwd=backend, capture_output=True, text=True)
    assert stale.returncode != 0, "a version bump must leave the lock detectably stale"

    refresh = _release_step("Refresh the lock for the new version").replace("git diff --stat -- uv.lock", "")
    subprocess.run(["bash", "-ec", refresh], cwd=backend, check=True, capture_output=True)

    assert _selections(backend / "uv.lock") == _selections(REPO / "backend" / "uv.lock")
    [project] = [p for p in tomllib.loads((backend / "uv.lock").read_text())["package"] if p["name"] == "bioaf-backend"]
    assert project["version"] == "2099.1.0"


# ── dependency updates ──


def test_dependabot_proposes_weekly_backend_lock_updates():
    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())
    assert config["version"] == 2
    [backend] = [u for u in config["updates"] if u["package-ecosystem"] == "uv"]
    assert backend["directory"] == "/backend"
    assert backend["schedule"]["interval"] == "weekly"


def test_dependabot_respects_the_deliberate_pins():
    # ruff, mypy, kubernetes and boto3 move only by a deliberate pyproject change (see the comments there).
    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())
    [backend] = [u for u in config["updates"] if u["package-ecosystem"] == "uv"]
    ignored = {i["dependency-name"]: i.get("update-types") for i in backend.get("ignore", [])}
    assert set(ignored) >= {"ruff", "mypy", "kubernetes", "boto3"}
    assert ignored["kubernetes"] == ["version-update:semver-major"]
    assert ignored["boto3"] == ["version-update:semver-major"]


def test_the_refresh_process_is_documented():
    text = (REPO / "backend" / "DEPENDENCIES.md").read_text()
    for command in ("uv lock --check", "uv lock --upgrade-package", "uv sync --locked"):
        assert command in text
    assert "0.13.5" in text
