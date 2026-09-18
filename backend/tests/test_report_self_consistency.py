"""plan_8_3 (Recovery and reporting): one report never contradicts itself.

Study 55's summary read "No analysis inputs were acquired and no scientific claims were tested" beside
its own record of a consistency check that AGREED with the paper's 194-gene count, and its claims line
said "none tested". Study 56 reported that no supplementary attachments were identified beside a
section listing the two its paper names. Both sentences are derived from one source each and are false
about the report they sit in.

The invariants are enforced in the projection and checked here over the real reports: where any check
record is conclusive, no sentence says that no claims were tested; where any supplement row exists, no
sentence says that none were identified; and where a claim was checked without an analysis input being
acquired, the summary says that rather than reporting an absence of work.

A contradiction between two sections of one report is a defect, not a wording preference.
"""

import json
import pathlib

import pytest

from app.services.validation_report_summary import report_contradictions, summarize
from tests.replay import SAVED_STUDIES, replay_report, restore

_CONTRACT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "validation"
    / "__fixtures__"
    / "reportContract.json"
)


async def _report(session, admin_user, study_id: int) -> dict:
    restored = await restore(session, study_id, organization_id=admin_user.organization_id, user_id=admin_user.id)
    return await replay_report(session, restored)


class TestNoSavedStudysReportContradictsItself:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("study_id", sorted(SAVED_STUDIES))
    async def test_the_projection_reports_no_contradiction(self, session, admin_user, study_id):
        report = await _report(session, admin_user, study_id)
        assert report_contradictions(report) == []

    @pytest.mark.asyncio
    async def test_every_report_of_the_checked_in_contract_holds_too(self, session, admin_user):
        """The contract fixture is what the frontend's tests render; a breach there ships to the page."""
        contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
        reports = {k: v for k, v in contract.items() if isinstance(v, dict) and "summary" in v}
        assert reports
        for name, report in reports.items():
            assert report_contradictions(report) == [], name


class TestAConclusiveCheckIsNeverReportedAsNoClaimTested:
    @pytest.mark.asyncio
    async def test_groffs_summary_says_what_was_checked(self, session, admin_user):
        report = await _report(session, admin_user, 55)
        summary = " ".join(report["summary"])
        assert "no scientific claims were tested" not in summary
        assert "1 scientific claim was checked against the authors' published results" in summary

    @pytest.mark.asyncio
    async def test_it_still_says_no_analysis_input_was_acquired(self, session, admin_user):
        """A consistency check acquires no analysis input. Both halves are true and both are said."""
        report = await _report(session, admin_user, 55)
        assert "No analysis inputs were acquired" in " ".join(report["summary"])

    @pytest.mark.asyncio
    async def test_the_claims_line_does_not_say_none_tested(self, session, admin_user):
        report = await _report(session, admin_user, 55)
        label = report["claim_counts"]["label"]
        assert "1 checked against the authors' published results" in label
        # "none tested" survives only as "none tested by reproduction", which is true and is not the
        # sentence that contradicted the record.
        assert "none tested." not in label
        assert report["claim_counts"]["checked"] == 1

    @pytest.mark.asyncio
    async def test_the_comparisons_row_does_not_say_no_claim_was_compared(self, session, admin_user):
        report = await _report(session, admin_user, 55)
        assert "no claim was compared" not in (report["comparisons"]["reason"] or "")
        assert "authors' published results" in (report["comparisons"]["reason"] or "")


class TestASupplementRowIsNeverReportedAsNoneIdentified:
    @pytest.mark.asyncio
    async def test_samd1_says_what_its_paper_names(self, session, admin_user):
        report = await _report(session, admin_user, 56)
        summary = " ".join(report["summary"])
        assert "No supplementary attachments were identified" not in summary
        assert "named in the paper" in summary

    @pytest.mark.asyncio
    async def test_a_paper_that_names_none_still_says_so(self, session, admin_user):
        report = await _report(session, admin_user, 57)
        assert report["artifacts"] == []
        assert "No supplementary attachments were identified." in report["summary"]


class TestTheInvariantsCatchABreach:
    """The check has to fail on a contradiction, or it proves nothing about the ones it passes."""

    def test_a_conclusive_check_beside_none_tested_is_a_breach(self):
        report = summarize(
            study={"state": "classified", "classification": "inconclusive"},
            evidence={},
            plan={},
            targets=[],
            issues=[],
        )
        report["claims"] = [{"consistency": {"outcome": "agree"}}]
        assert any("tested" in breach for breach in report_contradictions(report))

    def test_a_supplement_row_beside_none_identified_is_a_breach(self):
        report = summarize(
            study={"state": "classified", "classification": "inconclusive"},
            evidence={},
            plan={},
            targets=[],
            issues=[],
        )
        report["artifacts"] = [{"kind": "reference", "label": "Supplemental Table S1"}]
        assert any("identified" in breach for breach in report_contradictions(report))
