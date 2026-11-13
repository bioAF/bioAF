"""The strict per-module mypy gate actually rejects bad values in its modules.

``pyproject.toml`` relaxes mypy globally (eight error codes disabled, untyped defs and bare
generics allowed). A ``[[tool.mypy.overrides]]`` block re-enables the value checks for a
named list of modules. These tests copy each gated module, with deliberate mistakes appended,
into a scratch tree at the same module path and run mypy with the repository's own config:
the gated path must report each mistake, and the same code under an ungated module path must
not, which proves the override (not the global config) is doing the work. The CI command
``mypy . --ignore-missing-imports`` and the global ``import-untyped`` suppression are
unchanged; this gate says nothing about third-party import checking.
"""

import tomllib
from pathlib import Path

import pytest
from mypy import api as mypy_api

BACKEND = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND / "pyproject.toml"

GATED = ["app.services.file_type_utils", "app.services.qc.template_helpers", "app.services.samplesheet_inputs"]
ENABLED = {"assignment", "arg-type", "attr-defined", "union-attr", "return-value", "var-annotated", "dict-item"}

# Each probe is appended to the gated module; the expected code is what mypy must report.
PROBES = {
    "assignment": ("_probe_assignment: int = 'one'\n", "assignment"),
    "arg-type": ("def _probe_arg(x: int) -> int:\n    return x\n\n\n_probe_arg('one')\n", "arg-type"),
    "return-value": ("def _probe_return() -> int:\n    return 'one'\n", "return-value"),
    "union-attr": ("def _probe_union(x: str | None) -> str:\n    return x.upper()\n", "union-attr"),
    "dict-item": ("_probe_dict: dict[str, int] = {'a': 'one'}\n", "dict-item"),
    "untyped-def": ("def _probe_untyped(x):\n    return x\n", "no-untyped-def"),
    "bare-generic": ("def _probe_generic(x: dict) -> list:\n    return []\n", "type-arg"),
}


def _override() -> dict:
    config = tomllib.loads(PYPROJECT.read_text())["tool"]["mypy"]
    matches = [o for o in config.get("overrides", []) if set(GATED) <= set(o.get("module", []))]
    assert len(matches) == 1, "one override block must name every gated module"
    return matches[0]


def _run_mypy(tmp_path: Path, module: str, extra: str) -> str:
    """mypy's report for ``module`` (a copy of its gated source plus ``extra``), using the repo config."""
    source = BACKEND.joinpath(*GATED_SOURCE[module].split(".")).with_suffix(".py").read_text()
    relative = Path(*module.split(".")).with_suffix(".py")
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    for parent in relative.parents:
        if parent != Path("."):
            (tmp_path / parent / "__init__.py").touch()
    target.write_text(source + "\n\n" + extra)
    stdout, stderr, _ = mypy_api.run(
        [
            str(target),
            "--config-file",
            str(PYPROJECT),
            "--ignore-missing-imports",
            "--explicit-package-bases",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
        ]
    )
    return stdout + stderr


GATED_SOURCE = {module: module for module in GATED}
GATED_SOURCE["ungated.services.file_type_utils"] = "app.services.file_type_utils"


def test_the_override_names_the_gated_modules_and_re_enables_the_value_checks():
    override = _override()
    assert set(override["enable_error_code"]) >= ENABLED
    assert override["disallow_untyped_defs"] is True
    assert override["disallow_incomplete_defs"] is True
    assert override["disallow_any_generics"] is True
    assert override["warn_return_any"] is True


@pytest.fixture(autouse=True)
def _at_scratch_root(tmp_path, monkeypatch):
    # mypy names a module by its path from the working directory (--explicit-package-bases).
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("module", GATED)
def test_each_gated_module_passes_its_own_gate(tmp_path, module):
    output = _run_mypy(tmp_path, module, "")
    assert "Success" in output, output


@pytest.mark.parametrize("probe", sorted(PROBES))
@pytest.mark.parametrize("module", GATED)
def test_a_bad_value_in_a_gated_module_is_rejected(tmp_path, module, probe):
    code, expected = PROBES[probe]
    output = _run_mypy(tmp_path, module, code)
    assert f"[{expected}]" in output, output


@pytest.mark.parametrize("probe", ["assignment", "arg-type", "return-value", "dict-item"])
def test_the_same_mistake_outside_the_gate_passes_the_global_config(tmp_path, probe):
    code, expected = PROBES[probe]
    output = _run_mypy(tmp_path, "ungated.services.file_type_utils", code)
    assert f"[{expected}]" not in output, output
