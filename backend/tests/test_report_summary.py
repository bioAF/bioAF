"""change_7.3 section 11: one projection of the evidence, rendered the same way on every surface.

The UI and the markdown renderer kept their own label maps and logic, so a correction made in one
drifted from the other. The projection is a pure function of the persisted evidence, the plan and the
issues; the API response, the JSON export and the markdown export all carry what it says.

The Groff expectation is the owner's summary (decision 9), asserted as facts, not as wording:

    Reproduction not attempted. Raw data are deposited under controlled access and were unavailable
    to bioAF in this attempt. Four supplementary attachments were identified, but their download
    failed. No analysis inputs were acquired and no scientific claims were tested.
"""

import json
import pathlib

import pytest
import pytest_asyncio

from app.services.supplement_inventory import parse_jats_supplements, resolve_supplements
from app.services.validation_assessment import propagate_retrieval
from app.services.validation_capabilities import discover_capabilities
from app.services.validation_completion import completion_for
from app.services.validation_report_summary import enum_labels, summarize

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "groff"
_JATS = (_FIXTURES / "fulltext_jats.xml").read_text()
_STUDY_34 = json.loads((_FIXTURES / "study_34_persisted.json").read_text())

_EGA_DATASETS = '[{"accession_id":"EGAD00001005044","num_samples":54,"access_type":"controlled","is_released":true}]'
_EGA_FILES = "[" + ",".join('{"extension":"fastq.gz"}' for _ in range(108)) + "]"


class _NotFound(Exception):
    def __init__(self):
        super().__init__("Client error '404 Not Found'")
        self.response = type("R", (), {"status_code": 404})()


async def _ega(url):
    return _EGA_FILES if url.endswith("/files") else _EGA_DATASETS


async def _404(_url):
    raise _NotFound()


async def _groff_failed_evidence() -> dict:
    """Study 34's situation on the current build: the bundle 404s on every attempt."""
    ledger: list[dict] = []
    rows = await resolve_supplements("PMC6771404", parse_jats_supplements(_JATS), fetcher=_404, ledger=ledger)
    caps = await discover_capabilities(
        accessions=[{"accession": "EGAS00001003667", "provenance": "extracted"}],
        has_full_text=True,
        code_availability=[{"kind": "supplementary", "identifier": "Supplemental File S2", "stated_in": "methods"}],
        fetcher=_ega,
    )
    caps = propagate_retrieval(caps, rows)
    completion = completion_for(route="deposit", capabilities=caps, supplements=rows)
    return {
        "pmcid": "PMC6771404",
        "supplements": rows,
        "retrieval_ledger": ledger,
        "capabilities": caps,
        "completion": completion,
        "assessment": {
            "reconciliation": {"status": "performed", "reason": "r", "basis": "paper_text"},
            "contradiction_pass": {"checked": True, "pairs": ["x"]},
            "basis": "paper_text",
        },
    }


def _summary(evidence, *, state="classified", classification="access_restricted", analysis_run_id=None, targets=None):
    return summarize(
        study={"state": state, "classification": classification, "analysis_run_id": analysis_run_id},
        evidence=evidence,
        plan={},
        targets=targets or [],
        issues=[],
    )


class TestGroffReadsAsTheOwnerSummaryStatesIt:
    @pytest.mark.asyncio
    async def test_reproduction_was_not_attempted(self):
        summary = _summary(await _groff_failed_evidence())
        assert summary["facts"]["reproduction_attempted"] is False
        assert summary["headline"]["key"] == "reproduction_not_attempted"
        assert summary["headline"]["label"] == "Reproduction not attempted"

    @pytest.mark.asyncio
    async def test_raw_data_are_deposited_under_controlled_access_and_unavailable(self):
        raw = _summary(await _groff_failed_evidence())["facts"]["raw_data"]
        assert raw["deposited"] == "yes"
        assert raw["available_to_bioaf"] == "no"
        assert raw["access"] == ["controlled"]

    @pytest.mark.asyncio
    async def test_four_attachments_were_identified_and_their_download_failed(self):
        attachments = _summary(await _groff_failed_evidence())["facts"]["attachments"]
        assert attachments["identified"] == 4
        assert attachments["failed"] == 4

    @pytest.mark.asyncio
    async def test_no_inputs_were_acquired_and_no_claims_tested(self):
        facts = _summary(await _groff_failed_evidence())["facts"]
        assert facts["inputs_acquired"] is False
        assert facts["claims_tested"] == 0

    @pytest.mark.asyncio
    async def test_there_is_one_summary_sentence_per_fact(self):
        assert len(_summary(await _groff_failed_evidence())["summary"]) == 4

    @pytest.mark.asyncio
    async def test_the_index_page_is_not_an_attachment(self):
        summary = _summary(await _groff_failed_evidence())
        assert summary["index_pages"] == 1
        assert all(a["kind"] != "index" for a in summary["artifacts"])

    @pytest.mark.asyncio
    async def test_one_failure_is_one_notice_listing_its_attachments(self):
        failures = _summary(await _groff_failed_evidence())["retrieval_failures"]
        assert len(failures) == 1
        assert "Supplemental File S2" in failures[0]["artifacts"]
        assert failures[0]["technical_detail"]["http_status"] == 404
        assert failures[0]["technical_detail"]["attempts"] == 3

    @pytest.mark.asyncio
    async def test_the_resume_requirements_say_credentials_alone_will_not_do(self):
        resume = _summary(await _groff_failed_evidence())["resume"]
        assert resume["label"] == "Review and resume"
        text = " ".join(resume["requirements"])
        assert "cannot yet acquire data from EGA" in text
        assert "credentials alone will not" in text
        assert "retries the attachment download" in text

    @pytest.mark.asyncio
    async def test_processed_results_are_not_established(self):
        facts = {f["key"]: f for f in _summary(await _groff_failed_evidence())["completion_facts"]}
        assert facts["processed_results_available"]["value"] == "not_established"
        assert facts["reproduction_input_available"]["label"] == "Reproduction input acquired by bioAF"

    @pytest.mark.asyncio
    async def test_the_raw_read_leg_is_reported_though_it_was_not_chosen(self):
        limitations = _summary(await _groff_failed_evidence())["limitations"]
        context = [limitation for limitation in limitations if not limitation["governs"]]
        assert [limitation["leg"] for limitation in context] == ["pipeline"]
        assert context[0]["kind"] == "unsupported_acquisition"

    @pytest.mark.asyncio
    async def test_the_code_reads_as_four_statuses(self):
        [s2] = _summary(await _groff_failed_evidence())["code_sources"]
        assert set(s2["identification"]) == {"methods", "article_manifest"}
        assert s2["retrieval"]["status"] == "failed"
        assert s2["inspection"]["status"] == "not_inspected"
        assert s2["execution"]["status"] == "not_attempted"


class TestAStudyRecordedBeforeTheLedgerReadsCorrectly:
    """Study 34 as it was persisted: eight rows each carrying a copy of one 404."""

    def _summary(self):
        return summarize(
            study={"state": "classified", "classification": _STUDY_34["study"]["classification"]},
            evidence=_STUDY_34["evidence"],
            plan=_STUDY_34["reproduction_plan"],
            targets=_STUDY_34["reproduction_plan"]["comparison_targets"],
            issues=[],
        )

    def test_its_failure_is_one_group_with_the_recorded_reason(self):
        failures = self._summary()["retrieval_failures"]
        assert len(failures) == 1
        assert "404" in failures[0]["technical_detail"]["recorded_reason"]

    def test_its_citations_become_aliases_of_the_attachments(self):
        summary = self._summary()
        assert len([a for a in summary["artifacts"] if a["kind"] == "attachment"]) == 4
        assert summary["index_pages"] == 1

    def test_its_boolean_no_reads_as_not_established(self):
        facts = {f["key"]: f for f in self._summary()["completion_facts"]}
        assert facts["processed_results_available"]["value"] == "not_established"

    def test_its_missing_input_label_is_not_an_established_absence(self):
        limitation = self._summary()["limitations"][0]
        assert limitation["kind"] == "missing_input"
        assert limitation["label"] == "Not established"

    def test_the_headline_follows_the_attempt(self):
        assert self._summary()["headline"]["label"] == "Reproduction not attempted"

    def test_reconciliation_that_never_ran_says_so(self):
        assert (
            self._summary()["reconciliation"]["label"] == "Reconciliation not performed: no attachments were inspected"
        )

    def test_an_unrecorded_contradiction_pass_is_not_read_as_consistency(self):
        assert self._summary()["consistency"]["label"] == "Consistency not checked"

    def test_its_declined_claims_read_as_no_supported_metric(self):
        claims = self._summary()["claims"]
        declined = [c for c in claims if c["mapping"]["status"] == "no_supported_metric"]
        assert len(declined) == 4
        assert "bioAF has no metric that measures this claim" in declined[0]["mapping"]["explanation"]

    def test_mapped_is_not_tested(self):
        counts = self._summary()["claim_counts"]
        assert counts["mapped"] == 4
        assert counts["tested"] == 0
        assert counts["label"] == "4 claims mapped to candidate comparison metrics; none tested."

    def test_each_claim_leads_with_its_science(self):
        claim = self._summary()["claims"][0]
        assert claim["description"].startswith("The resulting data set includes 35 WE samples")
        assert claim["value"] == 54.0
        assert claim["population"] == "whole embryo and TE biopsy"


class TestAnAttemptThatReachedNoVerdict:
    def test_it_keeps_could_not_reproduce(self):
        summary = _summary({}, classification="inconclusive", analysis_run_id=7)
        assert summary["headline"]["key"] == "could_not_reproduce"
        assert summary["headline"]["label"] == "Could Not Reproduce"

    def test_it_never_says_not_attempted(self):
        summary = _summary({}, classification="inconclusive", analysis_run_id=7)
        assert "Reproduction not attempted." not in summary["summary"]


class TestEveryVocabularyHasALabel:
    def test_every_limitation_kind(self):
        from app.services.validation_completion import LIMITATION_KINDS

        assert set(LIMITATION_KINDS) <= set(enum_labels()["limitation_kind"])

    def test_every_issue_outcome(self):
        from app.models.validation_study_issue import ISSUE_OUTCOMES

        assert set(ISSUE_OUTCOMES) <= set(enum_labels()["issue_outcome"])

    def test_every_retrieval_status_and_outcome(self):
        from app.services.supplement_inventory import RETRIEVAL_OUTCOMES, RETRIEVAL_STATUSES

        assert set(RETRIEVAL_STATUSES) <= set(enum_labels()["retrieval_status"])
        assert set(RETRIEVAL_OUTCOMES) <= set(enum_labels()["retrieval_outcome"])

    def test_every_role(self):
        from app.services import supplement_inventory as inv

        roles = {
            inv.SAMPLE_METADATA,
            inv.EXPRESSION_MATRIX,
            inv.RESULTS_TABLE,
            inv.CODE,
            inv.SUPPORTING_INPUT,
            inv.UNKNOWN_ROLE,
        }
        assert roles <= set(enum_labels()["role"])

    def test_the_markdown_renderer_labels_every_issue_outcome_too(self):
        from app.models.validation_study_issue import ISSUE_OUTCOMES
        from app.services.provenance.markdown_renderer import _ISSUE_OUTCOME_LABEL

        assert set(ISSUE_OUTCOMES) <= set(_ISSUE_OUTCOME_LABEL)


class TestEverySurfaceSaysTheSameThing:
    """One fixture study, rendered through the API response, the JSON export and
    `MarkdownRenderer.render("validation_study", ...)`: the same headline, summary, limitations and
    artifact statuses in all three. Asserted through the entry points, never through a helper."""

    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    async def _study(self, session, admin_user):
        from app.services.reproduction_plan_service import ReproductionPlanService
        from app.services.validation_study_service import ValidationStudyService

        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_doi="10.1101/gr.252981.119"
        )
        plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, accessions=["EGAS00001003667"])
        await ReproductionPlanService.add_comparison_targets(
            session, plan, [dict(t) for t in _STUDY_34["reproduction_plan"]["comparison_targets"]]
        )
        study.reproduction_plan_id = plan.id
        study.state = "classified"
        study.classification = "access_restricted"
        study.evidence_json = await _groff_failed_evidence()
        await session.commit()
        return study

    async def _three(self, client, session, admin_user, admin_token):
        from app.services.provenance.report_service import ProvenanceReportService

        study = await self._study(session, admin_user)
        api = (
            await client.get(f"/api/validation-studies/{study.id}", headers={"Authorization": f"Bearer {admin_token}"})
        ).json()["report_summary"]
        exported = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=study.id,
            org_id=admin_user.organization_id,
            user_email=admin_user.email,
            format="json",
        )
        body = json.loads(exported.content)
        entity = body["report"]["entity"] if "report" in body else body["entity"]
        markdown = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=study.id,
            org_id=admin_user.organization_id,
            user_email=admin_user.email,
            format="md",
        )
        text = markdown.content if isinstance(markdown.content, str) else markdown.content.decode()
        return api, entity["report_summary"], text

    @pytest.mark.asyncio
    async def test_the_api_and_the_json_export_carry_the_same_projection(
        self, client, session, admin_user, admin_token
    ):
        api, exported, _ = await self._three(client, session, admin_user, admin_token)
        for key in ("headline", "summary", "limitations", "artifacts", "retrieval_failures", "claim_counts"):
            assert api[key] == exported[key], key

    @pytest.mark.asyncio
    async def test_the_markdown_states_the_same_headline_and_summary(self, client, session, admin_user, admin_token):
        api, _, text = await self._three(client, session, admin_user, admin_token)
        assert api["headline"]["label"] in text
        for sentence in api["summary"]:
            assert sentence in text

    @pytest.mark.asyncio
    async def test_the_markdown_states_the_same_limitations_and_statuses(
        self, client, session, admin_user, admin_token
    ):
        api, _, text = await self._three(client, session, admin_user, admin_token)
        for limitation in api["limitations"]:
            assert limitation["label"] in text
        for artifact in api["artifacts"]:
            assert artifact["label"] in text
            assert artifact["retrieval"]["label"] in text

    @pytest.mark.asyncio
    async def test_the_markdown_never_shows_the_failure_once_per_attachment(
        self, client, session, admin_user, admin_token
    ):
        _, _, text = await self._three(client, session, admin_user, admin_token)
        assert text.count("bioAF could not download the paper's supplementary files in this attempt") == 1

    @pytest.mark.asyncio
    async def test_the_markdown_says_reproduction_was_not_attempted_not_could_not_reproduce(
        self, client, session, admin_user, admin_token
    ):
        _, _, text = await self._three(client, session, admin_user, admin_token)
        assert "Reproduction not attempted" in text
        assert "Could Not Reproduce" not in text
        assert "undefined rows" not in text


class TestBlockersAndContrastsCarryTheirBasis:
    """change_7.3 section 6: targets, contrasts, checks and blockers carry their basis, and each renders
    as provisional while reconciliation has not run on inspected evidence."""

    def _summary(self, basis):
        return summarize(
            study={"state": "classified", "classification": "access_restricted"},
            evidence={"assessment": {"basis": basis}},
            plan={
                "blockers": ["Sample IDs are not enumerated in the text"],
                "blocker_kinds": [{"text": "Sample IDs are not enumerated in the text", "kind": "sample_assignment"}],
                "differential_design": {"contrasts": [{"name": "treated vs vehicle", "thresholds": {"padj": 0.05}}]},
            },
            targets=[],
            issues=[],
        )

    def test_a_blocker_from_the_prose_is_provisional(self):
        [blocker] = self._summary("paper_text")["blockers"]
        assert blocker["kind"] == "sample_assignment"
        assert blocker["provisional"] is True

    def test_a_contrast_from_the_prose_is_provisional(self):
        [contrast] = self._summary("paper_text")["contrasts"]
        assert contrast["name"] == "treated vs vehicle"
        assert contrast["provisional"] is True

    def test_both_settle_once_reconciled_on_inspected_evidence(self):
        summary = self._summary("inspected_evidence")
        assert summary["blockers"][0]["provisional"] is False
        assert summary["contrasts"][0]["provisional"] is False


class TestTheStudiesListFollowsTheAttemptToo:
    """change_7.3 section 10 item 1 on the list page: a classified study that executed nothing read
    "Could Not Reproduce" there, because the list carried only the bucket."""

    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    @pytest.mark.asyncio
    async def test_a_study_that_ran_nothing_is_listed_as_not_attempted(self, client, session, admin_user, admin_token):
        from app.services.validation_study_service import ValidationStudyService

        idle = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        idle.state, idle.classification = "classified", "access_restricted"
        ran = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        ran.state, ran.classification, ran.analysis_run_id = "classified", "inconclusive", 12
        await session.commit()

        rows = (await client.get("/api/validation-studies", headers={"Authorization": f"Bearer {admin_token}"})).json()
        by_id = {r["id"]: r for r in rows}
        assert by_id[idle.id]["attempt"] == "not_attempted"
        assert by_id[ran.id]["attempt"] == "attempted"
