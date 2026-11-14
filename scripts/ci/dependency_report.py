"""Apply the dated dependency-exception record to the PR dependency check and the scheduled npm audit.

``.github/security-exceptions.json`` is the only record of accepted dependency risk. Each entry names one advisory
(GHSA) in one package, the affected paths, why it is accepted, how it is mitigated, who owns it, who approved it
and when, and when it expires (at most 30 days after approval; an extension is a new approval). An entry is active
from its approval date until the day it expires; from that day it is inactive and reported, never an approval.

Two uses, standard library only:

``allow-ghsas``
    Prints ``ghsas=<comma-separated GHSA IDs>`` for ``actions/dependency-review-action``'s ``allow-ghsas`` input:
    only active entries whose approval explicitly covers introducing the advisory in a pull request
    (``allow_pr_introduction``). That input is advisory-wide (not per path or version) and has no expiry, so nothing
    else is forwarded. The PR job runs this from the base branch, so a pull request cannot grant itself an exception.

``audit``
    Classifies an ``npm audit --json`` report (with npm's exit status) against the record and writes a summary:
    every advisory found, and whether an active exception covers it. Exit 0 when every finding is covered,
    1 when any is not or an entry has expired, 2 when the report or the record cannot be trusted (a registry error,
    an unreadable or unexpected report, an exit status that does not match the report).

This adapter does not query advisories, resolve the dependency graph or match versions: npm and GitHub do that.
It prints advisory IDs, package names and states only, never registry responses or environment values.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

MAX_TERM = timedelta(days=30)
GHSA = re.compile(r"^GHSA(-[23456789cfghjmpqrvwx]{4}){3}$")
SEVERITIES = ("low", "moderate", "high", "critical")
SCOPES = ("runtime", "development", "unknown")
TEXT_FIELDS = ("package", "versions", "rationale", "mitigation", "owner", "approved_by", "approval_reference")


class PolicyError(Exception):
    """The exception record is missing, unreadable or invalid; it approves nothing."""


class ReportError(Exception):
    """The audit report cannot be trusted as a result."""


@dataclass(frozen=True)
class PolicyEntry:
    ghsa: str
    package: str
    expires: date
    allow_pr_introduction: bool
    state: str  # "active" or "expired"


@dataclass(frozen=True)
class Finding:
    ghsa: str
    package: str
    severity: str
    nodes: tuple[str, ...]


def _date(value: Any, field: str, where: str) -> date:
    if not isinstance(value, str):
        raise PolicyError(f"{where}: {field} must be a YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PolicyError(f"{where}: {field} must be a YYYY-MM-DD date") from exc


def _entry(raw: Any, index: int, today: date) -> PolicyEntry:
    where = f"exception {index}"
    if not isinstance(raw, dict):
        raise PolicyError(f"{where}: must be an object")
    ghsa = raw.get("ghsa")
    if not isinstance(ghsa, str) or not GHSA.match(ghsa):
        raise PolicyError(f"{where}: ghsa must be a GHSA identifier")
    for field in TEXT_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PolicyError(f"{where} ({ghsa}): {field} is required")
    paths = raw.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.strip() for p in paths):
        raise PolicyError(f"{where} ({ghsa}): paths must list the affected dependency paths")
    if raw.get("severity") not in SEVERITIES:
        raise PolicyError(f"{where} ({ghsa}): severity must be one of {', '.join(SEVERITIES)}")
    if raw.get("scope") not in SCOPES:
        raise PolicyError(f"{where} ({ghsa}): scope must be one of {', '.join(SCOPES)}")
    allow = raw.get("allow_pr_introduction")
    if not isinstance(allow, bool):
        raise PolicyError(f"{where} ({ghsa}): allow_pr_introduction must be true or false")
    approved = _date(raw.get("approval_date"), "approval_date", where)
    expires = _date(raw.get("expires"), "expires", where)
    if approved > today:
        raise PolicyError(f"{where} ({ghsa}): approval_date is in the future")
    if expires <= approved:
        raise PolicyError(f"{where} ({ghsa}): expires must be after approval_date")
    if expires - approved > MAX_TERM:
        raise PolicyError(f"{where} ({ghsa}): expires is more than 30 days after approval; extend by a new approval")
    state = "active" if today < expires else "expired"
    return PolicyEntry(ghsa, raw["package"], expires, allow, state)


def load_policy(path: Path, *, today: date) -> list[PolicyEntry]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError(f"{path.name} could not be read as JSON ({type(exc).__name__})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        raise PolicyError(f"{path.name} must be an object with an exceptions list")
    entries = [_entry(raw, i, today) for i, raw in enumerate(data["exceptions"])]
    seen: set[tuple[str, str]] = set()
    for e in entries:
        if (e.ghsa, e.package) in seen:
            raise PolicyError(f"{e.ghsa} for {e.package} is listed twice")
        seen.add((e.ghsa, e.package))
    return entries


def findings(path: Path, exit_status: int) -> list[Finding]:
    """One finding per advisory and package in an ``npm audit --json`` (report version 2) result."""
    if exit_status not in (0, 1):
        raise ReportError(f"npm audit exited {exit_status}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReportError(f"the audit report could not be read as JSON ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise ReportError("the audit report is not an object")
    if "error" in data:
        raise ReportError("npm audit reported an error instead of a result")
    vulns = data.get("vulnerabilities")
    if data.get("auditReportVersion") != 2 or not isinstance(vulns, dict):
        raise ReportError("the audit report is not an npm audit version 2 result")
    found: dict[tuple[str, str], Finding] = {}
    for entry in vulns.values():
        nodes = tuple(entry.get("nodes") or ())
        for via in entry.get("via") or ():
            if not isinstance(via, dict):
                continue  # affected only through another package, which carries the advisory
            ghsa = str(via.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
            if not GHSA.match(ghsa):
                raise ReportError("an advisory in the report has no GHSA identifier")
            key = (ghsa, str(via.get("name")))
            prior = found.get(key)
            found[key] = Finding(
                ghsa, key[1], str(via.get("severity")), tuple(sorted({*nodes, *(prior.nodes if prior else ())}))
            )
    if exit_status == 1 and not found:
        raise ReportError("npm audit failed but reported no advisory")
    return sorted(
        found.values(), key=lambda f: (SEVERITIES.index(f.severity) if f.severity in SEVERITIES else 9, f.ghsa)
    )


def classify(found: list[Finding], policy: list[PolicyEntry]) -> tuple[list[tuple[Finding, str]], list[PolicyEntry]]:
    """Each finding with its state, and the expired entries."""
    by_key = {(e.ghsa, e.package): e for e in policy}
    rows = []
    for f in found:
        e = by_key.get((f.ghsa, f.package))
        if e is None:
            rows.append((f, "unapproved"))
        elif e.state == "active":
            rows.append((f, f"excepted until {e.expires.isoformat()}"))
        else:
            rows.append((f, f"unapproved (exception expired {e.expires.isoformat()})"))
    return rows, [e for e in policy if e.state == "expired"]


def _summary(rows: list[tuple[Finding, str]], expired: list[PolicyEntry]) -> str:
    lines = ["### npm audit", ""]
    if rows:
        lines += ["| Advisory | Package | Severity | Installed at | State |", "| --- | --- | --- | --- | --- |"]
        lines += [
            f"| {f.ghsa} | {f.package} | {f.severity} | {', '.join(f.nodes) or '-'} | {state} |" for f, state in rows
        ]
    else:
        lines.append("No findings.")
    if expired:
        lines += ["", "Expired exceptions (inactive, renew by a new approval or remove):", ""]
        lines += [f"- {e.ghsa} ({e.package}), expired {e.expires.isoformat()}" for e in expired]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    allow = sub.add_parser("allow-ghsas", help="GHSA IDs the PR dependency check may allow")
    audit = sub.add_parser("audit", help="classify an npm audit --json report")
    audit.add_argument("--report", type=Path, required=True)
    audit.add_argument("--exit-status", type=int, required=True, help="npm audit's own exit status")
    audit.add_argument("--summary", help="append a markdown summary here (the job summary)")
    for p in (allow, audit):
        p.add_argument("--exceptions", type=Path, required=True)
        p.add_argument("--today", type=date.fromisoformat, default=None, help="evaluate as of this date (tests)")
    args = parser.parse_args(argv)
    today = args.today or date.today()

    try:
        policy = load_policy(args.exceptions, today=today)
    except PolicyError as exc:
        print(f"::error::Dependency exceptions are invalid, so none apply: {exc}")
        return 2

    if args.mode == "allow-ghsas":
        for e in policy:
            if e.state == "expired":
                print(f"::warning::Exception {e.ghsa} ({e.package}) expired {e.expires.isoformat()}", file=sys.stderr)
        eligible = sorted({e.ghsa for e in policy if e.state == "active" and e.allow_pr_introduction})
        print(f"ghsas={','.join(eligible)}")
        return 0

    try:
        found = findings(args.report, args.exit_status)
    except ReportError as exc:
        print(f"::error::The npm audit result cannot be trusted, so nothing was verified: {exc}")
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as out:
                out.write(f"### npm audit\n\nNo result: {exc}. Nothing was verified.\n")
        return 2
    rows, expired = classify(found, policy)
    text = _summary(rows, expired)
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as out:
            out.write(text)
    failing = [f for f, state in rows if state.startswith("unapproved")]
    for f in failing:
        print(f"::error::{f.ghsa} in {f.package} ({f.severity}) has no active exception")
    for e in expired:
        print(f"::error::Exception {e.ghsa} ({e.package}) expired {e.expires.isoformat()}")
    return 1 if failing or expired else 0


if __name__ == "__main__":
    sys.exit(main())
