"""Build the coverage comment CI posts on a pull request.

Reads the backend's coverage.py JSON (``--cov-report=json``), the frontend's
Istanbul JSON (``jest --coverage --coverageReporters=json``) and a unified diff
against the base branch, and prints one markdown comment: line coverage for
each half, the same split by top-level area, and coverage of the lines the pull
request added.

Standard library only, so it runs on a bare runner:

    python scripts/ci/coverage_report.py --backend coverage.json \\
        --frontend coverage-final.json --diff pr.diff --commit "$SHA" \\
        --backend-status 0 --frontend-status 0
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# The first line of every comment; CI finds and updates the comment by it.
MARKER = "<!-- bioaf-coverage-report -->"

# Files listed with uncovered changed lines, per half, and characters of line ranges per file.
MAX_FILES = 25
MAX_RANGES = 300

# GitHub refuses comments over 65536 characters; stay well clear of it.
MAX_BODY = 60000


@dataclass
class Side:
    """Line coverage for one half of the repo, keyed by path relative to that half."""

    name: str
    repo_prefix: str  # path of this half from the repo root, e.g. "backend/"
    area_root: str  # path segment areas are taken under, e.g. "app/"
    stops_on_failure: bool = False  # the backend runs pytest -x; Jest runs every suite
    # path -> (executable lines, covered lines)
    files: dict[str, tuple[set[int], set[int]]] = field(default_factory=dict)
    available: bool = True

    def total(self) -> tuple[int, int]:
        covered = sum(len(c) for _, c in self.files.values())
        return covered, sum(len(e) for e, _ in self.files.values())

    def area(self, path: str) -> str:
        rest = path[len(self.area_root) :] if path.startswith(self.area_root) else path
        return rest.split("/", 1)[0] if "/" in rest else "(top level)"

    def by_area(self) -> dict[str, tuple[int, int]]:
        areas: dict[str, tuple[int, int]] = {}
        for path, (executable, covered) in self.files.items():
            c, t = areas.get(self.area(path), (0, 0))
            areas[self.area(path)] = (c + len(covered), t + len(executable))
        return dict(sorted(areas.items()))

    def changed(self, added: dict[str, set[int]]) -> tuple[int, int, dict[str, list[int]]]:
        """(covered, executable, uncovered lines per file) among the lines a diff added."""
        covered = total = 0
        uncovered: dict[str, list[int]] = {}
        for repo_path, lines in added.items():
            if not repo_path.startswith(self.repo_prefix):
                continue
            path = repo_path[len(self.repo_prefix) :]
            if path not in self.files:
                continue
            executable, hit = self.files[path]
            relevant = lines & executable
            total += len(relevant)
            covered += len(relevant & hit)
            missing = sorted(relevant - hit)
            if missing:
                uncovered[path] = missing
        return covered, total, uncovered


def load_backend(path: Path) -> Side:
    side = Side("Backend", "backend/", "app/", stops_on_failure=True)
    if not path.is_file():
        side.available = False
        return side
    for name, data in json.loads(path.read_text())["files"].items():
        excluded = set(data.get("excluded_lines", []))
        executed = set(data.get("executed_lines", [])) - excluded
        executable = (executed | set(data.get("missing_lines", []))) - excluded
        side.files[name] = (executable, executed)
    return side


def load_frontend(path: Path) -> Side:
    side = Side("Frontend", "frontend/", "src/")
    if not path.is_file():
        side.available = False
        return side
    for name, data in json.loads(path.read_text()).items():
        # Istanbul keys are absolute; keep the part from the frontend's src/ on.
        marker = "/frontend/"
        relative = name.split(marker, 1)[1] if marker in name else name
        executable: set[int] = set()
        covered: set[int] = set()
        for sid, span in data.get("statementMap", {}).items():
            line = span["start"]["line"]
            executable.add(line)
            if data.get("s", {}).get(sid, 0) > 0:
                covered.add(line)
        side.files[relative] = (executable, covered)
    return side


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(diff: str) -> dict[str, set[int]]:
    """Repo path -> line numbers added, from a ``git diff --unified=0`` text."""
    added: dict[str, set[int]] = {}
    current: str | None = None
    line_no = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = target[2:] if target.startswith("b/") else None
            continue
        match = _HUNK.match(line)
        if match:
            line_no = int(match.group(1))
            continue
        if current and line.startswith("+"):
            added.setdefault(current, set()).add(line_no)
            line_no += 1
    return added


def ranges(lines: list[int]) -> str:
    parts: list[str] = []
    start = prev = None
    for n in sorted(lines):
        if prev is not None and n == prev + 1:
            prev = n
            continue
        if start is not None:
            parts.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = n
    if start is not None:
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def _pct(covered: int, total: int) -> str:
    return f"{100 * covered / total:.1f}%" if total else "n/a"


def _row(label: str, covered: int, total: int) -> str:
    return f"| {label} | {_pct(covered, total)} | {covered} / {total} |"


def _status_note(side: Side, status: str) -> str | None:
    if not side.available:
        return f"{side.name}: no coverage data (the job did not produce it)."
    if status not in ("", "0"):
        why = " (the run stops at the first failure)" if side.stops_on_failure else ""
        return f"{side.name}: tests failed, so coverage is partial{why}."
    return None


def render(
    backend: Side,
    frontend: Side,
    diff: str,
    commit: str,
    backend_status: str,
    frontend_status: str,
) -> str:
    sides = (backend, frontend)
    out = [MARKER, "## Coverage", "", f"Line coverage for `{commit[:7]}`.", ""]
    out += [
        "Backend: executable statement lines (coverage.py). Frontend: line coverage (Jest/Istanbul).",
        "Area totals measure execution, not assertion quality or branch coverage.",
        "",
    ]
    notes = [
        n
        for n in (
            _status_note(backend, backend_status),
            _status_note(frontend, frontend_status),
        )
        if n
    ]
    if notes:
        out += [f"> **Partial:** {n}" for n in notes] + [""]

    out += ["| Part | Lines | Covered / total |", "| --- | --- | --- |"]
    for side in sides:
        out.append(_row(side.name, *side.total()) if side.available else f"| {side.name} | not available | |")

    added = added_lines(diff)
    changed = [(side, side.changed(added)) for side in sides if side.available]
    out += ["", "### Lines changed in this pull request", ""]
    if not any(total for _, (_, total, _) in changed):
        out.append("No changed code lines are measured by coverage.")
    else:
        out += ["| Part | Lines | Covered / total |", "| --- | --- | --- |"]
        for side, (covered, total, _) in changed:
            if total:
                out.append(_row(side.name, covered, total))
        for side, (_, _, uncovered) in changed:
            if not uncovered:
                continue
            out += [
                "",
                f"<details><summary>{side.name}: changed lines not covered</summary>",
                "",
            ]
            for path in sorted(uncovered)[:MAX_FILES]:
                listed = ranges(uncovered[path])
                if len(listed) > MAX_RANGES:
                    listed = listed[:MAX_RANGES].rsplit(", ", 1)[0] + ", ..."
                out.append(f"- `{side.repo_prefix}{path}`: {listed}")
            if len(uncovered) > MAX_FILES:
                out.append(f"- and {len(uncovered) - MAX_FILES} more files")
            out += ["", "</details>"]

    for side in sides:
        if not side.available:
            continue
        out += [
            "",
            f"<details><summary>{side.name} by area (<code>{side.area_root}</code>)</summary>",
            "",
        ]
        out += ["| Area | Lines | Covered / total |", "| --- | --- | --- |"]
        out += [_row(area, c, t) for area, (c, t) in side.by_area().items()]
        out += ["", "</details>"]
    body = "\n".join(out) + "\n"
    if len(body) > MAX_BODY:  # the tables always fit; only the line listings can grow without bound
        body = body[:MAX_BODY].rsplit("\n", 1)[0]
        if body.count("<details>") > body.count("</details>"):
            body += "\n\n</details>"
        body += "\n\n_Report truncated to fit a GitHub comment._\n"
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--diff", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--backend-status", default="")
    parser.add_argument("--frontend-status", default="")
    args = parser.parse_args(argv)
    diff = args.diff.read_text() if args.diff.is_file() else ""
    print(
        render(
            load_backend(args.backend),
            load_frontend(args.frontend),
            diff,
            args.commit,
            args.backend_status.strip(),
            args.frontend_status.strip(),
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
