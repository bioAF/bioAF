"""Dependency vulnerability policy: the dated exception record and the npm audit report
(scripts/ci/dependency_report.py, .github/security-exceptions.json).

The PR check (actions/dependency-review-action) fails on newly introduced high or critical risk; only an active,
explicitly approved exception can allow an advisory there. The scheduled audit reports everything in the current
tree. In both, a missing, malformed or failed report is never a clean result, and an expired exception is never an
approval.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "dependency_report.py"
POLICY = REPO / ".github" / "security-exceptions.json"
TODAY = date(2026, 11, 20)


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location("dependency_report", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry(**overrides) -> dict:
    entry = {
        "ghsa": "GHSA-gcqx-w48m-w2h4",
        "package": "deepmerge",
        "paths": ["jest > jest-cli > jest-config > deepmerge"],
        "versions": "<=4.3.1",
        "severity": "low",
        "scope": "development",
        "rationale": "No patched release exists.",
        "mitigation": "Only Jest's own configuration reaches it.",
        "owner": "brent-mills-engineering",
        "approved_by": "Brent Mills",
        "approval_reference": "owner approval 2026-11-14",
        "approval_date": "2026-11-14",
        "expires": "2026-12-14",
        "allow_pr_introduction": False,
    }
    entry.update(overrides)
    return entry


def _policy(tmp_path: Path, *entries: dict) -> Path:
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"exceptions": list(entries)}))
    return path


def _advisory(ghsa: str, name: str, severity: str) -> dict:
    return {
        "source": 1,
        "name": name,
        "dependency": name,
        "title": "t",
        "url": f"https://github.com/advisories/{ghsa}",
        "severity": severity,
        "range": "<9",
    }


def _audit(tmp_path: Path, vulns: dict) -> Path:
    path = tmp_path / "audit.json"
    path.write_text(json.dumps({"auditReportVersion": 2, "vulnerabilities": vulns, "metadata": {}}))
    return path


DEEPMERGE = {
    "deepmerge": {
        "name": "deepmerge",
        "severity": "low",
        "via": [_advisory("GHSA-gcqx-w48m-w2h4", "deepmerge", "low")],
        "nodes": ["node_modules/deepmerge"],
    },
    # A package affected only through a dependency carries no advisory of its own.
    "jest-config": {
        "name": "jest-config",
        "severity": "low",
        "via": ["deepmerge"],
        "nodes": ["node_modules/jest-config"],
    },
}
NEXT = {
    "next": {
        "name": "next",
        "severity": "critical",
        "via": [_advisory("GHSA-2464-fhr4-w9xw", "next", "critical")],
        "nodes": ["node_modules/next"],
    }
}


# --- the exception record ---------------------------------------------------------------------------------


def test_the_committed_policy_is_valid(report):
    assert report.load_policy(POLICY, today=date.today()) is not None


def test_an_entry_is_active_from_approval_until_the_day_it_expires(report, tmp_path):
    path = _policy(tmp_path, _entry())
    assert [e.state for e in report.load_policy(path, today=date(2026, 11, 14))] == ["active"]
    assert [e.state for e in report.load_policy(path, today=date(2026, 12, 13))] == ["active"]
    assert [e.state for e in report.load_policy(path, today=date(2026, 12, 14))] == ["expired"]


@pytest.mark.parametrize(
    "broken",
    [
        {"ghsa": "CVE-2026-1"},
        {"ghsa": ""},
        {"owner": ""},
        {"approval_reference": " "},
        {"mitigation": None},
        {"paths": []},
        {"approval_date": "14/11/2026"},
        {"expires": "2026-11-14"},  # not after approval
        {"expires": "2027-01-14"},  # longer than 30 days from approval
        {"approval_date": "2026-11-30", "expires": "2026-12-20"},  # approved in the future
        {"severity": "severe"},
        {"scope": "everything"},
        {"allow_pr_introduction": "yes"},
    ],
)
def test_an_invalid_entry_invalidates_the_policy(report, tmp_path, broken):
    with pytest.raises(report.PolicyError):
        report.load_policy(_policy(tmp_path, _entry(**broken)), today=TODAY)


def test_a_missing_field_or_unreadable_file_invalidates_the_policy(report, tmp_path):
    entry = _entry()
    del entry["rationale"]
    with pytest.raises(report.PolicyError):
        report.load_policy(_policy(tmp_path, entry), today=TODAY)
    unreadable = tmp_path / "bad.json"
    unreadable.write_text("{not json")
    with pytest.raises(report.PolicyError):
        report.load_policy(unreadable, today=TODAY)
    with pytest.raises(report.PolicyError):
        report.load_policy(tmp_path / "missing.json", today=TODAY)


def test_a_duplicate_advisory_and_package_invalidates_the_policy(report, tmp_path):
    with pytest.raises(report.PolicyError):
        report.load_policy(_policy(tmp_path, _entry(), _entry()), today=TODAY)


# --- the PR check's allowance -----------------------------------------------------------------------------


def test_only_active_entries_approved_for_pr_introduction_are_allowed(report, tmp_path, capsys):
    path = _policy(
        tmp_path,
        _entry(),  # active, but not approved for introduction in a PR
        _entry(ghsa="GHSA-2222-3333-4444", allow_pr_introduction=True),
        _entry(
            ghsa="GHSA-5555-6666-7777",
            allow_pr_introduction=True,
            approval_date="2026-10-01",
            expires="2026-10-31",
        ),
    )
    assert report.main(["allow-ghsas", "--exceptions", str(path), "--today", "2026-11-20"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "ghsas=GHSA-2222-3333-4444"
    assert "GHSA-5555-6666-7777" in out.err and "expired" in out.err


def test_no_eligible_entry_allows_nothing(report, tmp_path, capsys):
    assert report.main(["allow-ghsas", "--exceptions", str(_policy(tmp_path, _entry())), "--today", "2026-11-20"]) == 0
    assert capsys.readouterr().out.strip() == "ghsas="


def test_an_invalid_policy_fails_the_pr_check(report, tmp_path):
    assert report.main(["allow-ghsas", "--exceptions", str(_policy(tmp_path, _entry(owner="")))]) == 2


# --- the scheduled audit ----------------------------------------------------------------------------------


def _run_audit(report, tmp_path, vulns, status, *entries, today="2026-11-20"):
    summary = tmp_path / "summary.md"
    code = report.main(
        [
            "audit",
            "--exceptions",
            str(_policy(tmp_path, *entries)),
            "--report",
            str(_audit(tmp_path, vulns)),
            "--exit-status",
            str(status),
            "--summary",
            str(summary),
            "--today",
            today,
        ]
    )
    return code, summary.read_text()


def test_a_clean_audit_passes(report, tmp_path):
    code, summary = _run_audit(report, tmp_path, {}, 0)
    assert code == 0
    assert "No findings" in summary


def test_a_finding_covered_by_an_active_exception_passes_and_is_still_listed(report, tmp_path):
    code, summary = _run_audit(report, tmp_path, DEEPMERGE, 1, _entry())
    assert code == 0
    assert "GHSA-gcqx-w48m-w2h4" in summary and "2026-12-14" in summary


def test_an_unapproved_finding_fails(report, tmp_path):
    code, summary = _run_audit(report, tmp_path, {**DEEPMERGE, **NEXT}, 1, _entry())
    assert code == 1
    assert "GHSA-2464-fhr4-w9xw" in summary and "unapproved" in summary


def test_an_expired_exception_is_not_an_approval(report, tmp_path):
    code, summary = _run_audit(report, tmp_path, DEEPMERGE, 1, _entry(), today="2026-12-14")
    assert code == 1
    assert "expired" in summary


def test_an_exception_for_another_package_does_not_cover_a_finding(report, tmp_path):
    code, _ = _run_audit(report, tmp_path, DEEPMERGE, 1, _entry(package="other-package"))
    assert code == 1


@pytest.mark.parametrize(
    "body",
    [
        "",
        "{not json",
        json.dumps({"error": {"code": "ENOTFOUND", "summary": "registry unreachable"}}),
        json.dumps({"auditReportVersion": 2, "metadata": {}}),
        json.dumps({"auditReportVersion": 1, "advisories": {}}),
    ],
)
def test_an_unreadable_or_failed_report_is_an_error_not_a_clean_result(report, tmp_path, body, capsys):
    audit = tmp_path / "audit.json"
    audit.write_text(body)
    code = report.main(
        ["audit", "--exceptions", str(_policy(tmp_path, _entry())), "--report", str(audit), "--exit-status", "1"]
    )
    assert code == 2
    assert "registry unreachable" not in capsys.readouterr().out  # registry text is not echoed


def test_a_nonzero_exit_without_findings_is_an_error(report, tmp_path):
    code, _ = _run_audit(report, tmp_path, {}, 1)
    assert code == 2


def test_an_unexpected_exit_status_is_an_error(report, tmp_path):
    code, _ = _run_audit(report, tmp_path, DEEPMERGE, 137, _entry())
    assert code == 2
