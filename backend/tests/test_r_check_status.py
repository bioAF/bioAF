"""The R SDK's R CMD check gate (scripts/ci/r_check_status.py).

Errors fail. So do the license, dependency-declaration and documentation defects plan 10 repaired, if
they come back. Other warnings and notes are listed for review without failing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "r_check_status.py"

CLEAN = """\
* using log directory '/work/bioaf.Rcheck'
* checking for file 'bioaf/DESCRIPTION' ... OK
* checking package dependencies ... INFO
Packages suggested but not available for checking: 'arrow'
* checking tests ...
  Running 'testthat.R'
 OK
* DONE

Status: OK
"""


def _log(*blocks: str, status: str) -> str:
    return "* using log directory '/work/bioaf.Rcheck'\n" + "".join(blocks) + f"* DONE\n\nStatus: {status}\n"


LICENSE = "* checking DESCRIPTION meta-information ... WARNING\nInvalid license file pointers: LICENSE\n"
IMPORTS = (
    "* checking dependencies in R code ... WARNING\n'::' or ':::' imports not declared from:\n  'curl' 'methods'\n"
)
# R uses the singular form when exactly one package is undeclared.
IMPORT = "* checking dependencies in R code ... WARNING\n'::' or ':::' import not declared from: 'methods'\n"
UNDOCUMENTED = (
    "* checking for missing documentation entries ... WARNING\nUndocumented code objects:\n"
    "  'extract_seurat_metadata'\n"
)
UNEXPORTED = "* checking dependencies in R code ... WARNING\nMissing or unexported object: 'Seurat::Layers'\n"
TESTS_FAILED = "* checking tests ... ERROR\n  Running 'testthat.R'\nRunning the tests in 'tests/testthat.R' failed.\n"
NOTE = "* checking R code for possible problems ... NOTE\nno visible binding for global variable 'x'\n"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("r_check_status", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(gate, tmp_path: Path, text: str) -> tuple[int, str]:
    log = tmp_path / "00check.log"
    log.write_text(text)
    summary = tmp_path / "summary.md"
    status = gate.main([str(log), "--summary", str(summary)])
    return status, summary.read_text()


def test_a_clean_check_passes(gate, tmp_path):
    status, summary = _run(gate, tmp_path, CLEAN)
    assert status == 0
    assert "Status: OK" in summary


def test_an_error_fails(gate, tmp_path):
    status, summary = _run(gate, tmp_path, _log(TESTS_FAILED, status="1 ERROR"))
    assert status == 1
    assert "checking tests" in summary


@pytest.mark.parametrize(
    "block", [LICENSE, IMPORTS, IMPORT, UNDOCUMENTED], ids=["license", "imports", "one-import", "documentation"]
)
def test_a_repaired_defect_coming_back_fails(gate, tmp_path, block):
    status, _ = _run(gate, tmp_path, _log(block, status="1 WARNING"))
    assert status == 1


def test_other_warnings_and_notes_are_listed_without_failing(gate, tmp_path):
    status, summary = _run(gate, tmp_path, _log(UNEXPORTED, NOTE, status="1 WARNING, 1 NOTE"))
    assert status == 0
    assert "Seurat::Layers" in summary
    assert "no visible binding" in summary
    assert "WARNING" in summary and "NOTE" in summary


def test_a_check_that_never_finished_fails(gate, tmp_path):
    status, _ = _run(gate, tmp_path, "* using log directory '/work/bioaf.Rcheck'\n* checking tests ...\n")
    assert status == 1


def test_a_missing_log_fails(gate, tmp_path):
    assert gate.main([str(tmp_path / "absent.log")]) == 1
