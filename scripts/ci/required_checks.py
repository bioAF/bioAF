"""Fail the merge gate unless every job it depends on succeeded.

The ruleset on main requires the Coverage Report check, and that job needs every other job in ci.yml
(held by backend/tests/test_ci_workflow.py). It runs with ``always()``, so GitHub runs it whatever its
dependencies did; this script is what turns a failed, cancelled or skipped dependency into a failed check.
Only ``success`` passes.

The one exception is a planned skip: ``--allowed-skips-json`` lists jobs the change classifier
(scripts/ci/classify_changes.py) left unrun for a documentation or CI-configuration pull request, and a listed job may
be ``skipped``. The list comes only from a successful classification. It never excuses a failure, a cancellation or a
missing result, a malformed list fails the gate, and jobs named by ``--never-skip`` can never be on it.

Standard library only, so it runs on a bare runner:

    python3 scripts/ci/required_checks.py --needs "$NEEDS_JSON" --allowed-skips-json "$ALLOWED_SKIPS" \
        --never-skip changes,security-scan --summary "$GITHUB_STEP_SUMMARY"

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


def permitted_skips(raw: str | None, jobs: set[str], never: set[str]) -> set[str] | str:
    """The jobs allowed to be skipped, or an error message. Absent or empty permission allows nothing."""
    if raw is None or raw == "":
        return set()
    try:
        listed = json.loads(raw)
    except json.JSONDecodeError:
        return "the allowed skips are not JSON"
    if not isinstance(listed, list) or not all(isinstance(job, str) for job in listed):
        return "the allowed skips are not a list of job names"
    if len(set(listed)) != len(listed):
        return "the allowed skips repeat a job"
    unknown = sorted(set(listed) - jobs)
    if unknown:
        return f"the allowed skips name jobs this gate does not need: {', '.join(unknown)}"
    forbidden = sorted(set(listed) & never)
    if forbidden:
        return f"the allowed skips name jobs that must always run: {', '.join(forbidden)}"
    return set(listed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--needs", required=True, help="the job's needs context as JSON")
    parser.add_argument("--allowed-skips-json", help="JSON list of jobs the change classifier allowed to skip")
    parser.add_argument("--never-skip", default="", help="comma-separated jobs that may never be skipped")
    parser.add_argument("--summary", help="append a markdown table here (the job summary)")
    args = parser.parse_args(argv)

    found = results(args.needs)
    if found is None:
        print("::error::Required checks: the needs context is empty or unreadable; nothing was verified.")
        return 1
    never = {job.strip() for job in args.never_skip.split(",") if job.strip()}
    skips = permitted_skips(args.allowed_skips_json, set(found), never)
    if isinstance(skips, str):
        print(f"::error::Required checks: {skips}; no skip was allowed.")
        return 1

    for job, result in sorted(found.items()):
        print(f"{job}: {result or 'missing'}")
    if args.summary:
        rows = [f"| {job} | {result or 'missing'} |" for job, result in sorted(found.items())]
        with open(args.summary, "a", encoding="utf-8") as out:
            out.write("\n### Required checks\n\n| Job | Result |\n| --- | --- |\n" + "\n".join(rows) + "\n")

    failed = sorted(
        job for job, result in found.items() if result != "success" and not (result == "skipped" and job in skips)
    )
    if failed:
        print(f"::error::Required checks did not succeed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
