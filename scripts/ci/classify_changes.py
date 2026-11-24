"""Select the CI mode for a pull request from everything it changes.

Three modes, from cheapest to strongest:

- ``docs``: only allowlisted documentation changed. Markdown lint and the always-on checks run; no application
  tests, services or dependency installation.
- ``ci-config``: only CI configuration that is not a workflow (Dependabot, markdownlint), optionally with
  documentation. Workflow linters and the focused workflow regressions also run.
- ``full``: anything else, including every workflow file, this classifier, unknown paths and an empty diff. Every
  ci.yml job runs.

The whole pull request is classified against its merge base (``base...head``), with renames split into their old
and new paths. The exact label ``ci:full`` forces full validation. Any failure (unreadable diff or event) exits
non-zero without writing a mode, so the merge gate fails instead of granting a cheaper run.

Standard library only:

    python3 scripts/ci/classify_changes.py --base "$BASE_SHA" --head "$HEAD_SHA" --event "$GITHUB_EVENT_PATH" \\
        --output "$GITHUB_OUTPUT" --summary "$GITHUB_STEP_SUMMARY"
    python3 scripts/ci/classify_changes.py report --mode docs --needs "$NEEDS_JSON" --output coverage-report.md

Changing these lists changes what CI runs, so this file itself always selects full validation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

FORCE_FULL_LABEL = "ci:full"
REPORT_MARKER = "<!-- bioaf-coverage-report -->"

DOC_ROOT_FILES = frozenset({"README.md", "CONTRIBUTING.md", "CLAUDE.md", "GEMINI.md", "RELEASE_NOTES.md"})
DOC_TREES = ("docs/", "decisions/", "ai_guides/")  # *.md at any depth
DOC_FLAT_DIRS = ("changes/unreleased/",)  # *.md immediately inside
DOC_FILES = frozenset(
    {".github/pull_request_template.md", "assets/bioAF_Architecture.svg", "assets/bioAF_Architecture.png"}
)
CI_CONFIG_FILES = frozenset({".github/dependabot.yml", ".markdownlint.json"})

MODES = ("docs", "ci-config", "full")  # weakest to strongest

# ci.yml jobs a cheaper mode may leave unrun. Every other job (the classifier, secret scanning, dependency review,
# markdown lint and the merge gate) runs on every pull request.
APPLICATION_JOBS = (
    "terraform-validate",
    "backend-lint",
    "backend-test",
    "frontend-lint",
    "frontend-test",
    "python-sdk",
    "r-sdk",
    "shell-tests",
    "update-agent-systemd",
)
SKIPS = {
    "full": (),
    "ci-config": APPLICATION_JOBS,
    "docs": (*APPLICATION_JOBS, "workflow-lint"),
}


def classify_path(path: str) -> str:
    """The mode one repository-relative path needs. Matching is exact and case-sensitive."""
    if path in DOC_ROOT_FILES or path in DOC_FILES:
        return "docs"
    if path.endswith(".md"):
        if path.startswith(DOC_TREES):
            return "docs"
        for directory in DOC_FLAT_DIRS:
            if path.startswith(directory) and "/" not in path[len(directory) :]:
                return "docs"
    if path in CI_CONFIG_FILES:
        return "ci-config"
    return "full"


@dataclass
class Result:
    mode: str
    reasons: list[str] = field(default_factory=list)


def classify(paths: list[str], labels: list[str]) -> Result:
    if FORCE_FULL_LABEL in labels:
        return Result("full", [f"the {FORCE_FULL_LABEL} label forces full validation"])
    if not paths:
        return Result("full", ["no changed paths were found; full validation is the safe choice"])
    by_mode: dict[str, list[str]] = {m: [] for m in MODES}
    for path in sorted(set(paths)):
        by_mode[classify_path(path)].append(path)
    mode = max((m for m in MODES if by_mode[m]), key=MODES.index)
    if mode == "full":
        reasons = [f"needs full validation: {p}" for p in by_mode["full"]]
    elif mode == "ci-config":
        reasons = [f"CI configuration: {p}" for p in by_mode["ci-config"]]
    else:
        reasons = ["documentation only"]
    return Result(mode, reasons)


def allowed_skips(mode: str) -> list[str]:
    return list(SKIPS[mode])


def changed_paths(base: str, head: str) -> list[str]:
    """Every path the pull request touches since its merge base; renames count as a deletion and an addition."""
    done = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in done.stdout.split("\0") if p]


def event_labels(event_path: str) -> list[str]:
    with open(event_path, encoding="utf-8") as f:
        event = json.load(f)
    labels = event["pull_request"].get("labels") or []
    return [str(label["name"]) for label in labels]


def _summary(result: Result, head: str) -> str:
    merge = os.environ.get("GITHUB_SHA", "unknown")
    lines = [
        "### CI mode",
        "",
        f"- Mode: **{result.mode}**",
        f"- Pull request head: `{head}`",
        f"- Tested merge commit: `{merge}`",
        f"- Jobs allowed to skip: {', '.join(allowed_skips(result.mode)) or 'none'}",
        "",
        *(f"- {r}" for r in result.reasons),
        "",
    ]
    return "\n".join(lines)


def mode_report(mode: str, needs_json: str) -> str:
    """The pull request comment for a cheaper mode: why tests and coverage did not run, and what did."""
    try:
        needs = json.loads(needs_json)
    except json.JSONDecodeError:
        needs = {}
    rows = [f"| {job} | {(value or {}).get('result') or 'missing'} |" for job, value in sorted(needs.items())]
    return "\n".join(
        [
            REPORT_MARKER,
            "## Coverage",
            "",
            f"This pull request was classified as **{mode}**: application tests and coverage were not required, so no "
            "coverage was measured. Add the `ci:full` label to run full validation.",
            "",
            "| Job | Result |",
            "| --- | --- |",
            *rows,
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["report"]:
        parser = argparse.ArgumentParser(description="write the cheaper-mode report")
        parser.add_argument("--mode", required=True, choices=["docs", "ci-config"])
        parser.add_argument("--needs", required=True)
        parser.add_argument("--output", required=True)
        args = parser.parse_args(argv[1:])
        with open(args.output, "w", encoding="utf-8") as out:
            out.write(mode_report(args.mode, args.needs))
        return 0

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--event", required=True, help="the pull_request event payload (GITHUB_EVENT_PATH)")
    parser.add_argument("--output", required=True, help="append mode= and allowed_skips= here (GITHUB_OUTPUT)")
    parser.add_argument("--summary", help="append the selected mode and its reasons here")
    args = parser.parse_args(argv)

    try:
        paths = changed_paths(args.base, args.head)
    except (OSError, subprocess.CalledProcessError):
        print("::error::Could not read the pull request's changes from git; no CI mode was selected.")
        return 1
    try:
        labels = event_labels(args.event)
    except (OSError, ValueError, KeyError, TypeError):
        print("::error::Could not read the pull request's labels from the event; no CI mode was selected.")
        return 1

    result = classify(paths, labels)
    print(f"mode={result.mode}")
    for reason in result.reasons:
        print(f"  {reason}")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as out:
            out.write(_summary(result, args.head))
    with open(args.output, "a", encoding="utf-8") as out:
        out.write(f"mode={result.mode}\nallowed_skips={json.dumps(allowed_skips(result.mode))}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
