"""Backend dependency authority (plan 11).

backend/pyproject.toml declares every backend dependency: runtime under [project].dependencies, development under
[dependency-groups].dev. backend/uv.lock resolves both, and every installer (CI, benchmarks, the backend image,
manual development) installs from that lock. These tests hold the declarations, the lock's agreement with them, and
the constraints whose reasons live in the comments next to them.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from functools import cache
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

BACKEND = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND / "pyproject.toml"
LOCK = BACKEND / "uv.lock"


@cache
def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


@cache
def _lock() -> dict:
    return tomllib.loads(LOCK.read_text())


def _requirements(specs: list[str]) -> dict[str, Requirement]:
    reqs = [Requirement(s) for s in specs]
    return {canonicalize_name(r.name): r for r in reqs}


def _runtime() -> dict[str, Requirement]:
    return _requirements(_pyproject()["project"]["dependencies"])


def _dev() -> dict[str, Requirement]:
    return _requirements(_pyproject()["dependency-groups"]["dev"])


def _lock_project() -> dict:
    [project] = [p for p in _lock()["package"] if p["name"] == "bioaf-backend"]
    return project


# ── one authority ──


def test_the_hand_maintained_requirements_lists_are_retired():
    assert not (BACKEND / "requirements.txt").exists()
    assert not (BACKEND / "requirements-dev.txt").exists()


def test_the_backend_is_an_application_not_a_package():
    # uv installs the dependencies only; the source runs from /app as copied, it is never built into a wheel.
    assert _pyproject()["tool"]["uv"]["package"] is False


def test_the_version_stays_a_literal_line_under_project():
    # Read by ./bioaf (tomllib and a grep fallback), app/config.py, the release workflow's sed and the historical
    # upgrade runner.
    text = PYPROJECT.read_text()
    project_table = text.split("[project]", 1)[1].split("\n[", 1)[0]
    assert re.search(r'^version = "\d{4}\.\d{1,2}\.\d+"$', project_table, re.MULTILINE)
    assert len(re.findall(r"^version = ", text, re.MULTILINE)) == 1


def test_the_lock_resolves_the_declared_runtime_and_development_dependencies():
    project = _lock_project()
    assert project["version"] == _pyproject()["project"]["version"]
    locked_runtime = {canonicalize_name(r["name"]) for r in project["metadata"]["requires-dist"]}
    locked_dev = {canonicalize_name(r["name"]) for r in project["metadata"]["requires-dev"]["dev"]}
    assert locked_runtime == set(_runtime())
    assert locked_dev == set(_dev())


def test_the_lock_keeps_artifact_hashes():
    for package in _lock()["package"]:
        if package.get("source", {}).get("registry"):
            artifacts = [package.get("sdist", {})] + package.get("wheels", [])
            assert any(a.get("hash", "").startswith("sha256:") for a in artifacts), package["name"]


# ── capabilities and constraints carried over from requirements.txt / requirements-dev.txt ──

RUNTIME_FLOORS = {
    # name: (minimum, why)
    "starlette": ("1.3.1", "GHSA-82w8-qh3p-5jfq, GHSA-86qp-5c8j-p5mr"),
    "cryptography": ("50.0.0", "GHSA-537c-gmf6-5ccf, GHSA-g6cj-pr64-35w5"),
    "pydantic-settings": ("2.14.2", "GHSA-4xgf-cpjx-pc3j"),
    "python-multipart": ("0.0.31", "GHSA-5rvq-cxj2-64vf"),
    "pyjwt": ("2.13.0", "GHSA-993g-76c3-p5m4, GHSA-fhv5-28vv-h8m8"),
    "pyasn1": ("0.6.4", "GHSA-m4p7-r5rc-7g4j"),
}


@pytest.mark.parametrize("name", sorted(RUNTIME_FLOORS))
def test_security_floors_are_declared_and_locked(name):
    floor, advisories = RUNTIME_FLOORS[name]
    requirement = _runtime()[name]
    assert f">={floor}" in str(requirement.specifier), advisories
    assert Version(_locked_version(name)) >= Version(floor), advisories


@pytest.mark.parametrize(
    ("name", "specifier"),
    [("boto3", "<2,>=1.34"), ("kubernetes", "<36,>=35.0")],
)
def test_compatibility_caps_are_kept(name, specifier):
    assert str(_runtime()[name].specifier) == specifier


@pytest.mark.parametrize(("name", "version"), [("ruff", "0.15.17"), ("mypy", "2.1.0")])
def test_lint_tools_stay_pinned(name, version):
    assert str(_dev()[name].specifier) == f"=={version}"
    assert _locked_version(name) == version


@pytest.mark.parametrize(
    ("name", "extra"),
    [("pydantic", "email"), ("pyjwt", "crypto"), ("uvicorn", "standard"), ("sqlalchemy", "asyncio")],
)
def test_runtime_extras_are_kept(name, extra):
    # pydantic[email] backs EmailStr in the API schemas.
    assert extra in _runtime()[name].extras


def test_botocore_moves_with_boto3():
    # botocore is imported directly (S3 and ECR adapters). It carries no range of its own: boto3 pins it exactly,
    # so a separate floor or cap could only conflict.
    botocore = _runtime()["botocore"]
    assert not botocore.specifier
    boto3_deps = {canonicalize_name(Requirement(d["name"]).name) for d in _locked_package("boto3")["dependencies"]}
    assert "botocore" in boto3_deps


# Retained although nothing in the repository imports them: scripts supplied to `./bioaf seed` may (plan 11 defers
# removals).
RETAINED_WITHOUT_IMPORTS = {"scanpy", "matplotlib", "pygithub"}


@pytest.mark.parametrize("name", sorted(RETAINED_WITHOUT_IMPORTS))
def test_capabilities_without_imports_are_retained(name):
    assert name in _runtime()


def test_development_tools_are_not_runtime_dependencies():
    assert not set(_dev()) & set(_runtime())


# ── every direct import is declared where it runs ──


@cache
def _module_owners() -> dict[str, set[str]]:
    """Installed file path (relative to site-packages, as a module) -> owning distributions."""
    owners: dict[str, set[str]] = {}
    for dist in metadata.distributions():
        name = canonicalize_name(dist.metadata["Name"])
        for file in dist.files or []:
            parts = file.parts
            if not parts or not file.name.endswith(".py") and not file.name.endswith(".so"):
                continue
            module = ".".join(parts[:-1] + (file.name.split(".", 1)[0],))
            module = module.removesuffix(".__init__")
            owners.setdefault(module, set()).add(name)
    return owners


def _imported_modules(roots: list[Path]) -> set[str]:
    modules: set[str] = set()
    for root in roots:
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(), str(path))):
                if isinstance(node, ast.Import):
                    modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    # `from google.cloud import storage` names a module inside a namespace package.
                    modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _owners_of(module: str) -> set[str]:
    owners = _module_owners()
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        found = owners.get(".".join(parts[:end]))
        if found:
            return found
    return set()


def _undeclared(roots: list[Path], declared: set[str]) -> dict[str, set[str]]:
    local = {"app", "tests"}
    missing = {}
    for module in sorted(_imported_modules(roots)):
        top = module.split(".", 1)[0]
        if top in sys.stdlib_module_names or top in local:
            continue
        owners = _owners_of(module)
        assert owners, f"{module} is imported but no installed distribution provides it"
        if not owners & declared:
            missing[module] = owners
    return missing


def test_every_application_import_is_a_declared_runtime_dependency():
    # A runtime import satisfied only transitively, or only by a development tool, breaks when that other package
    # changes. h5py, NumPy and botocore were in this state before plan 11.
    assert _undeclared([BACKEND / "app", BACKEND / "alembic"], set(_runtime())) == {}


def test_every_test_import_is_declared():
    assert _undeclared([BACKEND / "tests"], set(_runtime()) | set(_dev())) == {}


# ── helpers over the lock ──


def _locked_package(name: str) -> dict:
    [package] = [p for p in _lock()["package"] if canonicalize_name(p["name"]) == name]
    return package


def _locked_version(name: str) -> str:
    return _locked_package(name)["version"]
