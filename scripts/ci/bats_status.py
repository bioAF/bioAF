"""Gate the Shell Tests job from the Bats TAP output.

Fails on any ``not ok`` test, on a skip other than the network guard's ("ghcr.io unreachable"), and on a run
that executed no tests. Bats exits 0 when it finds no tests, so the last rule is what stops an emptied or
moved tests/shell from passing the merge gate.

Standard library only, so it runs on a bare runner:

    python3 scripts/ci/bats_status.py shell-evidence/bats-output.tap --summary "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

RESULT = re.compile(r"^(ok|not ok) \d+")
SKIP = re.compile(r"^ok \d+ .*# skip\b")
ACCEPTED_SKIP = "# skip ghcr.io unreachable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tap", help="the Bats TAP output")
    parser.add_argument("--summary", help="append a markdown report here (the job summary)")
    args = parser.parse_args(argv)

    path = Path(args.tap)
    if not path.is_file():
        print(f"::error::Bats output not found: {path}")
        return 1
    results = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if RESULT.match(line)]
    failed = [line for line in results if line.startswith("not ok")]
    skips = [line for line in results if SKIP.match(line)]
    unexpected = [line for line in skips if not line.endswith(ACCEPTED_SKIP)]
    executed = len(results) - len(skips)

    problems: list[str] = []
    if failed:
        problems.append(f"{len(failed)} Bats test(s) failed")
    if unexpected:
        problems.append("unexpected skips (only 'ghcr.io unreachable' is accepted): " + "; ".join(unexpected))
    if executed == 0:
        problems.append("no Bats tests executed")

    lines = [
        "### Shell tests (Bats)",
        "",
        f"- total: {len(results)}",
        f"- executed: {executed}",
        f"- failed: {len(failed)}",
        f"- skipped: {len(skips)}",
    ]
    if skips:
        lines += ["", "```", *skips, "```"]
    lines += [""] + ([f"FAILED: {p}" for p in problems] or ["PASSED"])
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
