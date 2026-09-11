"""change_7.3 section 11: every vocabulary has a label on both stacks, held by one checked-in contract.

The frontend renders the report projection; its tests render the fixture this test writes. So a label
the backend adds, renames or drops fails here until the fixture the frontend tests read is
regenerated, and a frontend map that lacks a value in the fixture's ``enums`` fails there.

Regenerate after an intended change with ``BIOAF_WRITE_REPORT_CONTRACT=1``.
"""

import json
import os
import pathlib

import pytest

_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "validation"
    / "__fixtures__"
    / "reportContract.json"
)
_FROZEN = "2026-09-10T13:09:18+00:00"


async def _contract() -> dict:
    from app.services.validation_report_summary import enum_labels, summarize
    from tests.test_report_summary import _STUDY_34, _groff_failed_evidence

    evidence = await _groff_failed_evidence()
    # Times are the one thing that differs between runs; the contract is about shape and wording.
    for entry in evidence["retrieval_ledger"]:
        entry["at"] = _FROZEN
    groff = summarize(
        study={"state": "classified", "classification": "access_restricted"},
        evidence=evidence,
        plan=_STUDY_34["reproduction_plan"],
        targets=_STUDY_34["reproduction_plan"]["comparison_targets"],
        issues=[],
    )
    legacy = summarize(
        study={"state": "classified", "classification": _STUDY_34["study"]["classification"]},
        evidence=_STUDY_34["evidence"],
        plan=_STUDY_34["reproduction_plan"],
        targets=_STUDY_34["reproduction_plan"]["comparison_targets"],
        issues=[],
    )
    attempted = summarize(
        study={"state": "classified", "classification": "inconclusive", "analysis_run_id": 7},
        evidence={},
        plan={},
        targets=[],
        issues=[],
    )
    return json.loads(
        json.dumps(
            {
                "enums": enum_labels(),
                "groff_failed": groff,
                "study_34_legacy": legacy,
                "attempted_no_verdict": attempted,
            }
        )
    )


@pytest.mark.skipif(not _FIXTURE.parent.parent.exists(), reason="the frontend is not checked out beside the backend")
@pytest.mark.asyncio
async def test_the_frontend_renders_the_projection_the_backend_produces():
    contract = await _contract()
    if os.environ.get("BIOAF_WRITE_REPORT_CONTRACT"):
        _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        _FIXTURE.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
    assert json.loads(_FIXTURE.read_text()) == contract, (
        "The report projection changed. Regenerate the frontend contract with BIOAF_WRITE_REPORT_CONTRACT=1 "
        "and make the frontend render it."
    )
