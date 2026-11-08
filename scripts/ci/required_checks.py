"""Fail the merge gate unless every job it depends on succeeded.

The ruleset on main requires the Coverage Report check, and that job needs every other job in ci.yml
(held by backend/tests/test_ci_workflow.py). It runs with ``always()``, so GitHub runs it whatever its
dependencies did; this script is what turns a failed, cancelled or skipped dependency into a failed check.
Only ``success`` passes.

Standard library only, so it runs on a bare runner:

    python3 scripts/ci/required_checks.py --needs "$NEEDS_JSON" --summary "$GITHUB_STEP_SUMMARY"

where ``NEEDS_JSON`` is ``${{ toJSON(needs) }}``.
"""

from __future__ import annotations

import argparse
import json


def results(raw: str) -> dict[str, str] | None:
    """Map each dependency to its result, or None when the needs context is unreadable or empty."""
    try:
        needs = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(needs, dict) or not needs:
        return None
    return {job: str((value or {}).get("result") or "") for job, value in needs.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--needs", required=True, help="the job's needs context as JSON")
    parser.add_argument("--summary", help="append a markdown table here (the job summary)")
    args = parser.parse_args(argv)

    found = results(args.needs)
    if found is None:
        print("::error::Required checks: the needs context is empty or unreadable; nothing was verified.")
        return 1

    for job, result in sorted(found.items()):
        print(f"{job}: {result or 'missing'}")
    if args.summary:
        rows = [f"| {job} | {result or 'missing'} |" for job, result in sorted(found.items())]
        with open(args.summary, "a", encoding="utf-8") as out:
            out.write("\n### Required checks\n\n| Job | Result |\n| --- | --- |\n" + "\n".join(rows) + "\n")

    failed = sorted(job for job, result in found.items() if result != "success")
    if failed:
        print(f"::error::Required checks did not succeed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
