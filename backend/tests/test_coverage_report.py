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


def test_backend_coverage_excludes_pragma_no_cover_lines(report, tmp_path):
    path = tmp_path / "excluded.json"
    path.write_text(
        json.dumps(
            {
                "files": {
                    "app/services/example.py": {
                        "executed_lines": [1, 2, 3],
                        "missing_lines": [4],
                        "excluded_lines": [2],
                    }
                }
            }
        )
    )
    side = report.load_backend(path)
    assert side.total() == (2, 3)
    assert side.changed({"backend/app/services/example.py": {2, 3, 4}}) == (1, 2, {"app/services/example.py": [4]})


def test_backend_default_coverage_traces_greenlets(tmp_path):
    """The config CI discovers must trace execution after a greenlet switches back."""
    import os
    import subprocess

    backend = Path(__file__).resolve().parents[1]
    from coverage import Coverage

    configuration = Coverage(config_file=str(backend / "pyproject.toml"))
    assert {"thread", "greenlet"} <= set(configuration.config.concurrency)
    probe = tmp_path / "greenlet_probe.py"
    probe.write_text(
        "from greenlet import greenlet, getcurrent\n"
        "def work():\n"
        "    getcurrent().parent.switch()\n"
        "    result = 42\n"
        "    return result\n"
        "g = greenlet(work)\n"
        "g.switch()\n"
        "g.switch()\n"
    )
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("COV_CORE_", "COVERAGE_")):
            env.pop(key)
    env["COVERAGE_FILE"] = str(tmp_path / ".coverage")
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--rcfile", str(backend / "pyproject.toml"), str(probe)],
        env=env,
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    from coverage import CoverageData

    data = CoverageData(basename=env["COVERAGE_FILE"])
    data.read()
    assert {3, 4, 5} <= set(data.lines(str(probe)) or [])


def test_backend_default_coverage_measures_branches(tmp_path):
    """The config CI discovers records which way each branch went, so branch results exist beside lines."""
    import os
    import subprocess

    backend = Path(__file__).resolve().parents[1]
    probe = tmp_path / "branch_probe.py"
    probe.write_text("value = 1\nif value:\n    value = 2\nprint(value)\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_CORE_", "COVERAGE_"))}
    env["COVERAGE_FILE"] = str(tmp_path / ".coverage")
    rcfile = str(backend / "pyproject.toml")
    for args in (["run", "--rcfile", rcfile, str(probe)], ["json", "--rcfile", rcfile, "-o", str(tmp_path / "c.json")]):
        result = subprocess.run(
            [sys.executable, "-m", "coverage", *args], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
    measured = json.loads((tmp_path / "c.json").read_text())["files"]["branch_probe.py"]
    # The if was only ever true: its false arc (line 2 to line 4) is the one missed branch.
    assert measured["summary"]["num_branches"] == 2
    assert measured["missing_branches"] == [[2, 4]]


# --- Branch results, reported beside line results and never folded into them ---


def _backend_with_branches(tmp_path: Path) -> Path:
    data = {
        "files": {
            "app/services/a.py": {
                "executed_lines": [1, 2],
                "missing_lines": [3],
                "summary": {"num_branches": 4, "covered_branches": 3},
            },
            "app/api/x.py": {
                "executed_lines": [1],
                "missing_lines": [],
                "summary": {"num_branches": 2, "covered_branches": 0},
            },
        }
    }
    path = tmp_path / "branch-coverage.json"
    path.write_text(json.dumps(data))
    return path


def _frontend_with_branches(tmp_path: Path) -> Path:
    root = "/home/runner/work/bioAF/bioAF/frontend"
    data = {
        f"{root}/src/lib/util.ts": {
            "statementMap": {"0": _statement(1)},
            "s": {"0": 1},
            # An if/else (one path taken) and a three-way switch (two paths taken): 3 of 5.
            "branchMap": {"0": {}, "1": {}},
            "b": {"0": [4, 0], "1": [1, 2, 0]},
        },
    }
    path = tmp_path / "frontend-branches.json"
    path.write_text(json.dumps(data))
    return path


def test_backend_branches_are_counted_from_each_files_summary(report, tmp_path):
    side = report.load_backend(_backend_with_branches(tmp_path))
    assert side.branch_total() == (3, 6)
    assert side.total() == (3, 4)  # lines are unchanged by branch data
    assert side.branches_by_area() == {"api": (0, 2), "services": (3, 4)}


def test_backend_data_without_branches_reports_branches_as_not_measured(report, tmp_path):
    side = report.load_backend(_backend(tmp_path))
    assert side.branch_total() is None
    body = report.render(
        backend=side,
        frontend=report.load_frontend(_frontend(tmp_path)),
        diff="",
        commit="abcdef1",
        backend_status="0",
        frontend_status="0",
    )
    assert "| Backend | 64.3% | 9 / 14 | not measured | |" in body


def test_frontend_branches_count_every_path_of_every_branch(report, tmp_path):
    side = report.load_frontend(_frontend_with_branches(tmp_path))
    assert side.branch_total() == (3, 5)


def test_the_comment_shows_line_and_branch_results_in_separate_columns(report, tmp_path):
    body = report.render(
        backend=report.load_backend(_backend_with_branches(tmp_path)),
        frontend=report.load_frontend(_frontend_with_branches(tmp_path)),
        diff="",
        commit="abcdef1",
        backend_status="0",
        frontend_status="0",
    )
    assert "| Part | Lines | Covered / total | Branches | Covered / total |" in body
    assert "| Backend | 75.0% | 3 / 4 | 50.0% | 3 / 6 |" in body
    assert "| Frontend | 100.0% | 1 / 1 | 60.0% | 3 / 5 |" in body
    assert "| services | 66.7% | 2 / 3 | 75.0% | 3 / 4 |" in body


# --- The reviewed unit selection, labeled apart from the combined suite ---


def _render(report, tmp_path, **overrides):
    kwargs = dict(
        backend=report.load_backend(_backend_with_branches(tmp_path)),
        frontend=report.load_frontend(_frontend_with_branches(tmp_path)),
        diff="",
        commit="abcdef1",
        backend_status="0",
        frontend_status="0",
    )
    kwargs.update(overrides)
    return report.render(**kwargs)


def test_the_unit_selection_is_reported_in_its_own_labeled_table(report, tmp_path):
    unit = report.load_unit(_backend(tmp_path))
    body = _render(report, tmp_path, unit=unit, unit_status="0")
    assert "### Unit selection" in body
    assert "| Backend unit selection | 64.3% | 9 / 14 | not measured | |" in body
    # The combined row is untouched by the unit result.
    assert "| Backend | 75.0% | 3 / 4 | 50.0% | 3 / 6 |" in body
    assert "all of `backend/app/`" in body


def test_a_failed_or_missing_unit_run_is_called_out_as_partial(report, tmp_path):
    failed = _render(report, tmp_path, unit=report.load_unit(_backend(tmp_path)), unit_status="1")
    assert "Backend unit selection: tests failed, so coverage is partial." in failed
    missing = _render(
        report,
        tmp_path,
        unit=report.load_unit(tmp_path / "none.json"),
        unit_status="",
    )
    assert "Backend unit selection: no coverage data" in missing


def test_without_a_unit_run_there_is_no_unit_table(report, tmp_path):
    assert "Unit selection" not in _render(report, tmp_path)


# --- Gates: floors per part and metric, met only by a complete, successful run ---


def _policy(*gates):
    return {"gates": [dict(zip(("part", "metric", "floor"), g)) for g in gates]}


def _sides(report, tmp_path):
    return {
        "backend": report.load_backend(_backend_with_branches(tmp_path)),  # lines 3/4, branches 3/6
        "frontend": report.load_frontend(_frontend_with_branches(tmp_path)),  # lines 1/1, branches 3/5
    }


def test_each_gate_compares_one_parts_one_metric_against_its_floor(report, tmp_path):
    sides = _sides(report, tmp_path)
    results = report.evaluate_gates(
        _policy(("Backend", "lines", 75.0), ("Backend", "branches", 51.0), ("Frontend", "branches", 60.0)),
        parts={"Backend": (sides["backend"], "0"), "Frontend": (sides["frontend"], "0")},
    )
    assert [(r.part, r.metric, r.passed) for r in results] == [
        ("Backend", "lines", True),  # exactly at the floor passes
        ("Backend", "branches", False),
        ("Frontend", "branches", True),
    ]
    assert "50.0% (3 / 6) is below the 51.0% floor" in results[1].detail


def test_a_floor_is_compared_on_raw_counts_not_a_rounded_percentage(report, tmp_path):
    sides = _sides(report, tmp_path)
    # 3 / 6 is exactly 50%; 50.0000001 must fail even though both round to 50.0%.
    [result] = report.evaluate_gates(
        _policy(("Backend", "branches", 50.0000001)), parts={"Backend": (sides["backend"], "0")}
    )
    assert not result.passed


@pytest.mark.parametrize("status", ["1", "", "143"])
def test_a_failed_interrupted_or_unknown_run_never_satisfies_a_gate(report, tmp_path, status):
    sides = _sides(report, tmp_path)
    [result] = report.evaluate_gates(_policy(("Backend", "lines", 0.0)), parts={"Backend": (sides["backend"], status)})
    assert not result.passed
    assert "not a complete, successful run" in result.detail


def test_missing_data_or_an_unmeasured_metric_fails_its_gate(report, tmp_path):
    missing = report.load_backend(tmp_path / "absent.json")
    no_branches = report.load_backend(_backend(tmp_path))
    results = report.evaluate_gates(
        _policy(("Backend", "lines", 0.0), ("Line-only", "branches", 0.0), ("Nowhere", "lines", 0.0)),
        parts={"Backend": (missing, "0"), "Line-only": (no_branches, "0")},
    )
    assert [r.passed for r in results] == [False, False, False]
    assert "no coverage data" in results[0].detail
    assert "branches were not measured" in results[1].detail
    assert "no such part" in results[2].detail


def test_an_unknown_metric_in_the_policy_is_refused(report):
    with pytest.raises(ValueError, match="functions"):
        report.evaluate_gates(_policy(("Backend", "functions", 50.0)), parts={})


def test_gate_results_are_rendered_and_enforcement_sets_the_exit_status(report, tmp_path):
    backend = tmp_path / "b.json"
    backend.write_text(_backend_with_branches(tmp_path).read_text())
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(_policy(("Backend", "lines", 75.0), ("Backend", "branches", 90.0))))
    args = ["--backend", str(backend), "--frontend", str(tmp_path / "none.json"), "--diff", str(tmp_path / "none")]
    args += ["--commit", "abcdef1", "--backend-status", "0", "--policy", str(policy)]

    reported = tmp_path / "report.md"
    assert report.main([*args, "--output", str(reported)]) == 0  # reported, not enforced
    body = reported.read_text()
    assert "### Gates" in body
    assert "| Backend | lines | 75.0% | 75.0% (3 / 4) | pass |" in body
    assert "| Backend | branches | 90.0% | 50.0% (3 / 6) | **fail** |" in body

    assert report.main([*args, "--output", str(reported), "--enforce"]) == 1
