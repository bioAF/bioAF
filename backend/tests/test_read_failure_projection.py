"""plan_8_1 section 1.4: one rule decides a failed read, and every surface renders it the same way.

Studies 42 and 43 are ``classified``, carry the absence blockers a failed read manufactured, and have none
of section 1.3's fields. The rule function matches them from what they do hold (a blocked paper-reading
issue and no claims), and every surface renders a matched study as a failed read, whatever its state:

- the stored absence blockers are withheld and listed as not established;
- the scorecard names the bioAF limitation and the next action;
- the classification is shown as produced by a failed read.

A study carrying explicit extraction provenance is judged by it, never by its old issues.
"""

import pytest
import pytest_asyncio

from app.services.validation_read_failure import read_failure
from app.services.validation_report_summary import scorecard_projection, summarize
from app.services.validation_scorecard import compact_scorecard

_READING = "reading the paper and extracting its methods and claims"
_TRUNCATED_ISSUE = {"step": _READING, "outcome": "truncated", "impact": "blocked", "message": "cut off", "model": "m"}
_ABSENCE_BLOCKERS = [
    "insufficient method detail to identify an assay",
    "could not parse a structured extraction from the model response",
    "no data accession found in the paper",
    "The paper does not state its reference, so it is unresolved: reanalysis from raw reads and QC metrics from a "
    "run are refused for this paper, and no default is used. Checks that need no reference are unaffected.",
]

# Shaped like study 42: classified missing_data on a failed read, with an unresolved inventory.
_STUDY_42 = {
    "study": {"state": "classified", "classification": "missing_data"},
    "evidence": {
        "precompute_checks": {},
        "scorecard_record": {"rubric_version": 1, "inventory_revision": 1, "outcomes": {}, "compact": {}},
    },
    "plan": {
        "accessions": [],
        "blockers": list(_ABSENCE_BLOCKERS),
        "finding_inventory": {
            "status": "unresolved",
            "revision": 1,
            "reason": "The paper could not be read into findings.",
        },
    },
    "targets": [],
    "issues": [_TRUNCATED_ISSUE],
}


def _summary(record: dict) -> dict:
    return summarize(
        study=record["study"],
        evidence=record["evidence"],
        plan=record["plan"],
        targets=record["targets"],
        issues=record["issues"],
    )


class TestTheRule:
    def test_explicit_failed_provenance_is_a_failed_read(self):
        evidence = {"extraction": {"status": "failed", "cause": "the answer was cut off"}}
        assert read_failure(evidence, issues=[], claim_count=0) == {"cause": "the answer was cut off", "legacy": False}

    def test_a_succeeded_current_extraction_with_no_claims_is_not_a_failed_read(self):
        """An earlier attempt's blocked issue stays history; it cannot mark the current plan."""
        evidence = {"extraction": {"status": "succeeded"}}
        assert read_failure(evidence, issues=[_TRUNCATED_ISSUE], claim_count=0) is None

    def test_an_extraction_in_progress_is_not_a_terminal_failure(self):
        evidence = {"extraction": {"status": "in_progress"}}
        assert read_failure(evidence, issues=[_TRUNCATED_ISSUE], claim_count=0) is None

    def test_the_legacy_rule_matches_a_study_shaped_like_42(self):
        found = read_failure(_STUDY_42["evidence"], issues=_STUDY_42["issues"], claim_count=0)
        assert found == {"cause": "the model's answer was cut off at its token limit", "legacy": True}

    def test_the_legacy_rule_needs_both_the_blocked_issue_and_no_claims(self):
        assert read_failure({}, issues=[_TRUNCATED_ISSUE], claim_count=3) is None
        assert read_failure({}, issues=[{**_TRUNCATED_ISSUE, "impact": "degraded"}], claim_count=0) is None
        assert read_failure({}, issues=[], claim_count=0) is None


class TestAMatchedStudyRendersAsAFailedRead:
    def test_the_absence_blockers_are_withheld_as_not_established(self):
        blockers = _summary(_STUDY_42)["blockers"]
        assert blockers[0]["kind"] == "bioaf_limitation"
        assert blockers[0]["text"] == (
            "bioAF could not read the paper: the model's answer was cut off at its token limit. This is a bioAF "
            "limitation, not a finding about the paper."
        )
        withheld = blockers[1:]
        assert [b["withheld"] for b in withheld] == _ABSENCE_BLOCKERS
        assert all(b["text"] == "Not established: the paper was not read." for b in withheld)
        assert all(b["kind"] == "not_established" for b in withheld)

    def test_the_scorecard_names_the_bioaf_limitation_and_the_next_action(self):
        card = _summary(_STUDY_42)["scorecard"]
        assert card["status"] == "not_established"
        assert card["reason"] == (
            "bioAF could not read the paper into findings: the model's answer was cut off at its token limit. This "
            "is a bioAF limitation; read the paper again."
        )
        assert (card["cause"], card["cause_label"]) == ("bioaf_limitation", "bioAF limitation")
        assert card["score_label"] is None

    def test_the_classification_is_shown_as_produced_by_a_failed_read(self):
        projected = _summary(_STUDY_42)["read_failure"]
        assert projected["cause"] == "the model's answer was cut off at its token limit"
        assert projected["classification_from_failed_read"] is True
        assert projected["classification_note"]

    def test_the_list_agrees_with_the_report(self):
        card = scorecard_projection(
            study=_STUDY_42["study"],
            evidence=_STUDY_42["evidence"],
            plan=_STUDY_42["plan"],
            targets=[],
            issues=_STUDY_42["issues"],
        )
        compact = compact_scorecard(card)
        assert compact["reason"] == _summary(_STUDY_42)["scorecard"]["reason"]
        assert compact["cause_label"] == "bioAF limitation"

    def test_a_new_failed_read_renders_the_same_way(self):
        blocker = "bioAF could not read the paper: the answer was cut off. This is a bioAF limitation, not a finding about the paper."
        record = {
            "study": {"state": "error"},
            "evidence": {"extraction": {"status": "failed", "cause": "the answer was cut off"}},
            "plan": {"blockers": [blocker], "blocker_kinds": [{"text": blocker, "kind": "bioaf_limitation"}]},
            "targets": [],
            "issues": [],
        }
        summary = _summary(record)
        assert [b["text"] for b in summary["blockers"]] == [blocker]
        assert summary["scorecard"]["reason"].startswith("bioAF could not read the paper into findings: the answer")
        assert summary["read_failure"]["classification_from_failed_read"] is False

    def test_a_study_whose_read_succeeded_is_untouched(self):
        record = {
            "study": {"state": "plan_ready"},
            "evidence": {"extraction": {"status": "succeeded"}},
            "plan": {"blockers": ["no data accession found in the paper"]},
            "targets": [],
            "issues": [_TRUNCATED_ISSUE],
        }
        summary = _summary(record)
        assert summary["read_failure"] is None
        assert [b["text"] for b in summary["blockers"]] == ["no data accession found in the paper"]


class TestTheExportsAndTheList:
    """Through their entry points: the page's API, the list API, the JSON export and the markdown."""

    @pytest_asyncio.fixture(autouse=True)
    async def _enable_lit_validation(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    async def _seed_study_42(self, session, user) -> int:
        from app.models.validation_study_issue import ValidationStudyIssue
        from app.services.reproduction_plan_service import ReproductionPlanService
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
        await ReproductionPlanService.create_plan(
            session,
            study,
            user.id,
            accessions=[],
            blockers=list(_ABSENCE_BLOCKERS),
            finding_inventory=dict(_STUDY_42["plan"]["finding_inventory"]),
        )
        session.add(
            ValidationStudyIssue(
                validation_study_id=study.id, step=_READING, outcome="truncated", impact="blocked", message="cut off"
            )
        )
        study.state = "classified"
        study.classification = "missing_data"
        study.evidence_json = dict(_STUDY_42["evidence"])
        await session.commit()
        return study.id

    @pytest.mark.asyncio
    async def test_the_page_the_list_the_json_and_the_markdown_agree(self, session, admin_user, client, admin_token):
        from app.services.provenance.data_gatherer import ProvenanceDataGatherer
        from app.services.provenance.json_renderer import JsonRenderer
        from app.services.provenance.markdown_renderer import MarkdownRenderer

        study_id = await self._seed_study_42(session, admin_user)
        headers = {"Authorization": f"Bearer {admin_token}"}
        page = (await client.get(f"/api/validation-studies/{study_id}", headers=headers)).json()["report_summary"]
        reason = page["scorecard"]["reason"]
        assert reason.startswith("bioAF could not read the paper into findings")

        listed = (await client.get("/api/validation-studies", headers=headers)).json()
        row = next(r for r in (listed if isinstance(listed, list) else listed["items"]) if r["id"] == study_id)
        assert row["scorecard"]["reason"] == reason
        assert row["scorecard"]["cause_label"] == "bioAF limitation"

        data = await ProvenanceDataGatherer.gather_validation_study(session, study_id, admin_user.organization_id)
        report = JsonRenderer.render("validation_study", data, "admin@test.com")
        exported = report["entity"]["report_summary"] if "entity" in report else report["report_summary"]
        assert exported["scorecard"]["reason"] == reason

        markdown = MarkdownRenderer.render("validation_study", report)
        assert reason in markdown
        blockers_line = next(line for line in markdown.splitlines() if line.startswith("**Blockers:**"))
        assert "no data accession found" not in blockers_line
        assert "Not established: the paper was not read." in blockers_line
