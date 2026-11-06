"""The CI coverage comment (scripts/ci/coverage_report.py): totals, areas, and changed lines for both halves."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "coverage_report.py"


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location("coverage_report", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while the class is built
    spec.loader.exec_module(module)
    return module


def _backend(tmp_path: Path) -> Path:
    data = {
        "files": {
            "app/main.py": {"executed_lines": [1, 2, 3], "missing_lines": [4]},
            "app/services/a.py": {"executed_lines": [1, 2], "missing_lines": [3, 4]},
            "app/services/b.py": {"executed_lines": [1, 2, 3, 4], "missing_lines": []},
            "app/api/x.py": {"executed_lines": [], "missing_lines": [1, 2]},
        }
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(data))
    return path


def _statement(line: int) -> dict:
    return {"start": {"line": line, "column": 0}, "end": {"line": line, "column": 9}}


def _frontend(tmp_path: Path) -> Path:
    root = "/home/runner/work/bioAF/bioAF/frontend"
    data = {
        f"{root}/src/lib/util.ts": {
            # Two statements on line 2 (one hit) count as one covered line.
            "statementMap": {"0": _statement(1), "1": _statement(2), "2": _statement(2), "3": _statement(5)},
            "s": {"0": 1, "1": 0, "2": 3, "3": 0},
        },
        f"{root}/src/components/Button.tsx": {
            "statementMap": {"0": _statement(1), "1": _statement(2)},
            "s": {"0": 1, "1": 1},
        },
    }
    path = tmp_path / "coverage-final.json"
    path.write_text(json.dumps(data))
    return path


DIFF = """\
diff --git a/backend/app/services/a.py b/backend/app/services/a.py
--- a/backend/app/services/a.py
+++ b/backend/app/services/a.py
@@ -1,0 +2,3 @@
+covered
+uncovered
+uncovered
diff --git a/frontend/src/lib/util.ts b/frontend/src/lib/util.ts
--- a/frontend/src/lib/util.ts
+++ b/frontend/src/lib/util.ts
@@ -4,0 +5 @@
+uncovered
@@ -10 +11 @@
+comment-only line, not executable
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
+not code
"""


def test_backend_lines_are_grouped_by_area(report, tmp_path):
    side = report.load_backend(_backend(tmp_path))
    assert side.total() == (9, 14)
    assert side.by_area() == {"(top level)": (3, 4), "api": (0, 2), "services": (6, 8)}


def test_frontend_lines_count_each_line_once_and_group_by_area(report, tmp_path):
    side = report.load_frontend(_frontend(tmp_path))
    assert side.total() == (4, 5)
    assert side.by_area() == {"components": (2, 2), "lib": (2, 3)}


def test_changed_lines_count_only_executable_added_lines(report, tmp_path):
    added = report.added_lines(DIFF)
    assert added["backend/app/services/a.py"] == {2, 3, 4}
    assert added["frontend/src/lib/util.ts"] == {5, 11}

    backend = report.load_backend(_backend(tmp_path))
    frontend = report.load_frontend(_frontend(tmp_path))
    assert backend.changed(added) == (1, 3, {"app/services/a.py": [3, 4]})
    assert frontend.changed(added) == (0, 1, {"src/lib/util.ts": [5]})


def test_the_comment_has_totals_areas_changed_lines_and_a_marker(report, tmp_path):
    body = report.render(
        backend=report.load_backend(_backend(tmp_path)),
        frontend=report.load_frontend(_frontend(tmp_path)),
        diff=DIFF,
        commit="abcdef1234567",
        backend_status="0",
        frontend_status="0",
    )
    assert body.startswith(report.MARKER)
    assert "abcdef1" in body
    assert "| Backend | 64.3% | 9 / 14 |" in body
    assert "| Frontend | 80.0% | 4 / 5 |" in body
    assert "| services | 75.0% | 6 / 8 |" in body
    assert "| Backend | 33.3% | 1 / 3 |" in body  # changed lines
    assert "`backend/app/services/a.py`: 3-4" in body
    assert "`frontend/src/lib/util.ts`: 5" in body
    assert "partial" not in body


def test_a_failed_or_missing_run_is_called_out(report, tmp_path):
    body = report.render(
        backend=report.load_backend(_backend(tmp_path)),
        frontend=report.load_frontend(tmp_path / "missing.json"),
        diff="",
        commit="abcdef1",
        backend_status="1",
        frontend_status="",
    )
    assert "partial" in body.lower()
    assert "| Frontend | not available |" in body
    assert "No changed code lines" in body


def test_ranges_collapse_consecutive_lines(report):
    assert report.ranges([1, 2, 3, 5, 7, 8]) == "1-3, 5, 7-8"


def test_only_the_backend_note_says_the_run_stopped_early(report, tmp_path):
    """The backend runs with -x; Jest runs every suite, so its failures do not cut coverage short."""
    kwargs = dict(
        backend=report.load_backend(_backend(tmp_path)),
        frontend=report.load_frontend(_frontend(tmp_path)),
        diff="",
        commit="abcdef1",
    )
    backend_failed = report.render(**kwargs, backend_status="1", frontend_status="0")
    frontend_failed = report.render(**kwargs, backend_status="0", frontend_status="1")
    assert "stops at the first failure" in backend_failed
    assert "stops at the first failure" not in frontend_failed
    assert "Frontend: tests failed" in frontend_failed


def test_the_comment_stays_under_githubs_size_limit(report, tmp_path):
    files = {
        f"app/services/s{i}.py": {"executed_lines": [], "missing_lines": list(range(1, 4000, 2))} for i in range(40)
    }
    path = tmp_path / "big.json"
    path.write_text(json.dumps({"files": files}))
    diff = "".join(f"+++ b/backend/app/services/s{i}.py\n@@ -0,0 +1,4000 @@\n" + "+x\n" * 4000 for i in range(40))
    body = report.render(
        backend=report.load_backend(path),
        frontend=report.load_frontend(tmp_path / "missing.json"),
        diff=diff,
        commit="abcdef1",
        backend_status="0",
        frontend_status="0",
    )
    assert len(body) <= report.MAX_BODY + 100
    assert body.count("<details>") == body.count("</details>")
    assert "| Backend | 0.0% | 0 / 80000 |" in body
