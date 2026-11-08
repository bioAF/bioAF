"""Gate the R SDK's ``R CMD check`` from its 00check.log.

Fails on any ERROR, on a check that never reached its Status line, and on the return of a defect plan 10
repaired (the LICENSE pointer, undeclared ``::`` imports, undocumented exports). Every other WARNING and
NOTE is listed in the summary for review without failing the job.

Standard library only, so it runs on a bare runner:

    python3 scripts/ci/r_check_status.py bioaf.Rcheck/00check.log --summary "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# The defects plan 10 repaired, by the text R CMD check reports for each. Their return fails the check.
REPAIRED = {
    "LICENSE file pointer": re.compile(r"Invalid license file pointers"),
    "undeclared :: imports": re.compile(r"imports? not declared from"),
    "undocumented exports": re.compile(r"Undocumented code objects"),
}

SEVERITY = re.compile(r"^\* (checking .*?) \.\.\. (ERROR|WARNING|NOTE)\s*$")
STATUS = re.compile(r"^Status: (.+)$", re.MULTILINE)


def diagnostics(log: str) -> list[tuple[str, str, str]]:
    """Each ERROR/WARNING/NOTE block as (severity, check, detail)."""
    found: list[tuple[str, str, str]] = []
    current: tuple[str, str] | None = None
    detail: list[str] = []
    for line in log.splitlines():
        if line.startswith("* "):
            if current:
                found.append((current[1], current[0], "\n".join(detail).strip()))
            match = SEVERITY.match(line)
            current = (match.group(1), match.group(2)) if match else None
            detail = []
        elif current:
            detail.append(line)
    if current:
        found.append((current[1], current[0], "\n".join(detail).strip()))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", help="bioaf.Rcheck/00check.log")
    parser.add_argument("--summary", help="append a markdown report here (the job summary)")
    args = parser.parse_args(argv)

    path = Path(args.log)
    if not path.is_file():
        print(f"::error::R CMD check log not found: {path}")
        return 1
    log = path.read_text(encoding="utf-8", errors="replace")

    status = STATUS.search(log)
    found = diagnostics(log)
    problems: list[str] = []
    if not status:
        problems.append("R CMD check did not finish (no Status line)")
    if any(severity == "ERROR" for severity, _, _ in found) or (status and "ERROR" in status.group(1)):
        problems.append("R CMD check reported an ERROR")
    problems += [f"repaired defect is back: {name}" for name, pattern in REPAIRED.items() if pattern.search(log)]

    lines = ["### R CMD check", "", f"Status: {status.group(1) if status else 'not reached'}", ""]
    for severity, check, detail in found:
        lines += [f"- **{severity}** {check}", "", "  ```", *[f"  {d}" for d in detail.splitlines()], "  ```", ""]
    lines += [f"FAILED: {p}" for p in problems] or ["PASSED (warnings and notes above are for review)"]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as out:
            out.write(report)

    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
