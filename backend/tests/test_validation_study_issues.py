"""plan_7 step 14a/14c: the steps that hit an error while validating a paper, on the record.

A refusal is invisible today. Some models decline biotech queries outright, and a model refusing
every claim binding produces a plan that silently fell back to the alias table with nothing on
screen. The same is true of a rate limit, a 502 and an answer that came back as prose.

The record is **study-scoped, not plan-scoped**. `AiDecisionList` is derived per-claim from
`comparison_target` rows, so it can only show issues that have a claim to hang off; a refusal while
choosing a deposited file or ratifying a verdict has no comparison target, and in some cases happens
before a plan exists at all.

It is a table rather than a key on `evidence_json` because two different writers append to it: the
request path, before the study has a plan, and the driver, which rewrites the whole evidence bundle
on nearly every tick.

**`impact` is what stops it crying wolf.** A step that fell back and carried on is not a step that
produced nothing, and a section that cannot tell them apart is one users learn to ignore.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.validation_study_issue import ValidationStudyIssue
from app.services.validation_issue_service import ValidationIssueService
from app.services.validation_study_service import ValidationStudyService


async def _study(session, admin_user):
    return await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1/issues"
    )


def _row(**kw):
    base = {
        "step": "binding the paper's claims to measurable metrics",
        "outcome": "refusal",
        "impact": "degraded",
        "message": "The model claude-opus-4-8 declined to answer.",
        "model": "claude-opus-4-8",
        "at": "2026-09-07T12:00:00+00:00",
    }
    base.update(kw)
    return base


class TestRecording:
    @pytest.mark.asyncio
    async def test_a_row_survives_to_the_database(self, session, admin_user):
        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [_row()])

        rows = (
            (
                await session.execute(
                    select(ValidationStudyIssue).where(ValidationStudyIssue.validation_study_id == study.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].outcome == "refusal"
        assert rows[0].impact == "degraded"
        assert rows[0].model == "claude-opus-4-8"

    @pytest.mark.asyncio
    async def test_a_refusal_names_the_model_an_admin_needs_an_exception_for(self, session, admin_user):
        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [_row(model="gpt-5")])
        listed = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert listed[0]["model"] == "gpt-5"

    @pytest.mark.asyncio
    async def test_nones_and_empties_record_nothing(self, session, admin_user):
        """Callers append `decision.as_issue(...)`, which is None when the call succeeded."""
        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [None, None])
        await ValidationIssueService.record(session, study, [])
        assert await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id) == []

    @pytest.mark.asyncio
    async def test_one_row_per_occurrence_including_the_same_step_twice(self, session, admin_user):
        """A re-ask that fails again is a second occurrence, not the same one. Collapsing them would
        hide that the retry was spent."""
        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [_row(), _row()])
        assert len(await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)) == 2

    @pytest.mark.asyncio
    async def test_rows_from_different_ticks_accumulate(self, session, admin_user):
        """The driver rewrites `evidence_json` wholesale on nearly every tick, which is exactly why
        this is not a key on it."""
        study = await _study(session, admin_user)
        await ValidationIssueService.record(
            session, study, [_row(step="choosing which deposited file to reproduce from")]
        )
        study.evidence_json = {"route": "deposit"}
        await session.flush()
        await ValidationIssueService.record(session, study, [_row(step="ratifying the measured verdict")])

        steps = [
            r["step"]
            for r in await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        ]
        assert steps == ["choosing which deposited file to reproduce from", "ratifying the measured verdict"]

    @pytest.mark.asyncio
    async def test_an_unknown_outcome_or_impact_is_refused_rather_than_stored(self, session, admin_user):
        """The vocabulary is what the report renders from. A value outside it would render as
        nothing at all, which is worse than not recording it."""
        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [_row(outcome="weird"), _row(impact="catastrophic")])
        assert await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id) == []


class TestScoping:
    @pytest.mark.asyncio
    async def test_another_org_lists_nothing(self, session, admin_user):
        """Org-scoped through the study, like every other read on this feature."""
        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [_row()])
        assert await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id + 999) == []


class TestTheDegradedAndBlockedSplit:
    @pytest.mark.asyncio
    async def test_both_are_stored_and_distinguishable(self, session, admin_user):
        study = await _study(session, admin_user)
        await ValidationIssueService.record(
            session,
            study,
            [
                _row(impact="degraded", step="binding the paper's claims to measurable metrics"),
                _row(impact="blocked", step="ratifying the measured verdict"),
            ],
        )
        listed = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert {r["impact"] for r in listed} == {"degraded", "blocked"}


class TestTheExtractionRecordsWhatItCouldNotGet:
    """The refusal path, end to end through the real extraction rather than the service alone."""

    @staticmethod
    def _patch(monkeypatch, *, submit):
        from types import SimpleNamespace

        from app.services import validation_extraction_service as ext

        async def _cfg(sess, org_id):
            return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return await submit(prompt)

        monkeypatch.setattr(ext.llm_provider_config_service, "get_active", _cfg)
        monkeypatch.setattr(ext, "get_client", lambda p: _C())

    @pytest.mark.asyncio
    async def test_a_refused_read_is_recorded_as_blocked_and_names_the_model(self, session, admin_user, monkeypatch):
        """Before this, a model that declined produced a plan with no pipeline and nothing anywhere
        saying a model had refused. The plan still gets its blocker; the reason is now on the record
        too, with the model an administrator has to ask about."""
        from app.services.llm_provider_clients import ProviderError
        from app.services.validation_extraction_service import ValidationExtractionService

        async def _refuse(prompt):
            raise ProviderError("I can't help with that.", error_class="refusal")

        self._patch(monkeypatch, submit=_refuse)
        study = await _study(session, admin_user)
        await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)

        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert [i["outcome"] for i in issues] == ["refusal"]
        assert issues[0]["impact"] == "blocked"
        assert "claude-opus-4-8" in issues[0]["message"]

    @pytest.mark.asyncio
    async def test_a_claim_binding_outage_is_recorded_as_degraded(self, session, admin_user, monkeypatch):
        """The binding falls back to the alias table and the plan is still usable, so this is a
        degrade. It used to be a `logger.warning` and nothing else."""
        from app.services.llm_provider_clients import ProviderError
        from app.services.validation_extraction_service import ValidationExtractionService

        extraction = (
            '```json\n{"accessions": ["GSE52778"], "method": {"assay": "bulk RNA-seq"}, '
            '"claims": [{"metric_key": "alignment_rate", "value": 93, "unit": "%"}], '
            '"data_availability": "public", "blockers": []}\n```'
        )

        async def _read_then_refuse(prompt):
            if prompt.startswith("You are binding a paper's quantitative claims"):
                raise ProviderError("rate limited", error_class="rate_limit")
            return extraction

        self._patch(monkeypatch, submit=_read_then_refuse)
        study = await _study(session, admin_user)
        await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)

        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert issues, "the binding degrade left no record at all"
        assert all(i["impact"] == "degraded" for i in issues)
        assert all(i["outcome"] == "unreachable" for i in issues)

    @pytest.mark.asyncio
    async def test_a_clean_read_records_nothing(self, session, admin_user, monkeypatch):
        """The section must stay empty when nothing went wrong, or it stops meaning anything."""
        from app.services.validation_extraction_service import ValidationExtractionService

        extraction = (
            '```json\n{"accessions": ["GSE52778"], "method": {"assay": "bulk RNA-seq"}, '
            '"claims": [], "data_availability": "public", "blockers": []}\n```'
        )

        async def _ok(prompt):
            return extraction

        self._patch(monkeypatch, submit=_ok)
        study = await _study(session, admin_user)
        await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)

        assert await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id) == []


class TestTheApiCarriesTheSection:
    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    @pytest.mark.asyncio
    async def test_the_study_response_lists_the_issues(self, client, session, admin_user, admin_token):
        """The study page and the export both read one place. Empty is the normal case."""
        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [_row()])
        await session.commit()

        r = await client.get(f"/api/validation-studies/{study.id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        issues = r.json()["issues"]
        assert len(issues) == 1
        assert issues[0]["step"] == "binding the paper's claims to measurable metrics"
        assert issues[0]["impact"] == "degraded"
        assert issues[0]["model"] == "claude-opus-4-8"

    @pytest.mark.asyncio
    async def test_a_clean_study_lists_none(self, client, session, admin_user, admin_token):
        study = await _study(session, admin_user)
        await session.commit()
        r = await client.get(f"/api/validation-studies/{study.id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.json()["issues"] == []


class TestTheExportCarriesTheSection:
    @pytest.mark.asyncio
    async def test_the_provenance_report_includes_them(self, session, admin_user):
        """An exported report missing the issues would be worse than the page missing them: the
        export is what leaves the building."""
        import json as _json

        from app.services.provenance.report_service import ProvenanceReportService

        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [_row(outcome="unreachable", impact="blocked")])

        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=study.id,
            org_id=admin_user.organization_id,
            user_email=admin_user.email,
            format="json",
        )
        body = _json.loads(result.content) if isinstance(result.content, (str, bytes)) else result.content
        entity = body["report"]["entity"] if "report" in body else body["entity"]
        assert entity["issues"][0]["outcome"] == "unreachable"
        assert entity["issues"][0]["impact"] == "blocked"

    @pytest.mark.asyncio
    async def test_the_markdown_export_names_them_in_plain_language(self, session, admin_user):
        from app.services.provenance.report_service import ProvenanceReportService

        study = await _study(session, admin_user)
        await ValidationIssueService.record(session, study, [_row()])

        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=study.id,
            org_id=admin_user.organization_id,
            user_email=admin_user.email,
            format="md",
        )
        text = result.content if isinstance(result.content, str) else result.content.decode()
        assert "Issues Encountered" in text
        assert "binding the paper's claims to measurable metrics" in text
        assert "claude-opus-4-8" in text


class TestTheExportCarriesTheWholeReport:
    """plan_7 step 19: the provenance export is not a second, thinner report.

    Step 12's coverage asserts the rendered report AND the export, because a report that is right on
    screen and lossy on export fails the same reader.
    """

    _EVIDENCE = {
        "route": "deposit",
        "capabilities": {
            "deposit_exists": {"value": "yes", "evidence": "GEO published a series record", "failure_reason": None},
            "preprocessed_data": {
                "value": "unknown",
                "evidence": None,
                "failure_reason": "bioAF could not reach GEO to list the supplementary files",
            },
            "code_sources": [
                {
                    "kind": "github",
                    "url": "https://github.com/lab/paper",
                    "identifier": None,
                    "exists": "yes",
                    "accessible": "no",
                    "accessible_reason": "the repository is private",
                }
            ],
        },
        "precompute_checks": {
            "species_matches": {
                "check": "species_matches",
                "verdict": "ok",
                "detail": "the paper and the deposit both name Homo sapiens",
                "blocking": False,
                "decided_by": "measurement",
                "model": None,
                "reason": "",
                "confidence": 0,
            }
        },
        "code_resolution": {
            "outcome": "resolved",
            "url": "https://github.com/lab/paper",
            "commit_sha": "abc1234",
            "reason": "pinned",
        },
        "code_execution": {
            "attempt": 1,
            "method": "authors_code",
            "qualifiers": ["methods_inadequate"],
            "outcome": "ran_output_diverges",
            "entry_point": "scripts/run.R",
            "source": {"repo_url": "https://github.com/lab/paper", "commit_sha": "abc1234"},
            "observation": {
                "outcome": "ran_output_diverges",
                "qualifiers": [],
                "exit_code": 0,
                "transcript_uri": "gs://x/log",
                "transcript_tail": "done",
                "metric": "peak_count",
                "paper_value": 7389,
                "our_value": 4054,
                "unmatched": [{"path": "summary.json", "name": "figure_ratio", "value": 0.4}],
            },
        },
        "execution_assessment": {
            "candidate": "bioaf_input_mapping",
            "candidates_offered": ["bioaf_input_mapping", "code_defect", "unresolved"],
            "reason": "we chose the input file and the column mapping",
            "confidence": 0.6,
            "model": "claude-opus-4-8",
            "assessed_at": "2026-09-07T00:00:00Z",
        },
        "signal_assessment": {
            "verdict": "likely",
            "reason": "weak enrichment",
            "confidence": 0.7,
            "model": "claude-opus-4-8",
            "assessed_at": "2026-09-07T00:00:00Z",
        },
    }

    async def _report(self, session, admin_user, fmt="md"):
        from app.services.provenance.report_service import ProvenanceReportService

        study = await _study(session, admin_user)
        study.evidence_json = self._EVIDENCE
        await session.flush()
        await ValidationIssueService.record(session, study, [_row()])
        result = await ProvenanceReportService.generate(
            session=session,
            entity_type="validation_study",
            entity_id=study.id,
            org_id=admin_user.organization_id,
            user_email=admin_user.email,
            format=fmt,
        )
        return result.content if isinstance(result.content, str) else result.content.decode()

    @pytest.mark.asyncio
    async def test_the_capability_checklist_is_in_the_export(self, session, admin_user):
        text = await self._report(session, admin_user)
        assert "Data deposit exists" in text
        assert "Yes" in text

    @pytest.mark.asyncio
    async def test_unknown_is_exported_as_unknown_with_its_reason(self, session, admin_user):
        """A checklist that shows NO for a GEO timeout tells the reader something false about the
        paper, on screen or in an export."""
        text = await self._report(session, admin_user)
        assert "Unknown" in text
        assert "could not reach GEO" in text

    @pytest.mark.asyncio
    async def test_existence_and_accessibility_stay_separate_in_the_export(self, session, admin_user):
        text = await self._report(session, admin_user)
        assert "the repository is private" in text

    @pytest.mark.asyncio
    async def test_the_code_section_reads_in_plain_language(self, session, admin_user):
        text = await self._report(session, admin_user)
        assert "scripts/run.R" in text
        assert "abc1234" in text
        assert "disagrees with the paper" in text

    @pytest.mark.asyncio
    async def test_both_numbers_are_exported_side_by_side(self, session, admin_user):
        text = await self._report(session, admin_user)
        assert "7389" in text or "7,389" in text
        assert "4054" in text or "4,054" in text

    @pytest.mark.asyncio
    async def test_the_explanation_is_exported_after_the_observation_and_hedged(self, session, admin_user):
        text = await self._report(session, admin_user)
        assert text.index("What happened") < text.index("What might explain it")
        assert "bioAF's own choice of input" in text

    @pytest.mark.asyncio
    async def test_a_possible_noise_flag_is_exported_hedged_not_as_a_verdict(self, session, admin_user):
        text = await self._report(session, admin_user)
        assert "Possible issue" in text
        assert "may" in text

    @pytest.mark.asyncio
    async def test_claims_we_could_not_compare_are_listed_not_omitted(self, session, admin_user):
        """A reader must be able to see the boundary of what was tested rather than infer a verdict
        from silence."""
        text = await self._report(session, admin_user)
        assert "figure_ratio" in text

    @pytest.mark.asyncio
    async def test_the_pre_compute_checks_are_exported(self, session, admin_user):
        text = await self._report(session, admin_user)
        assert "Homo sapiens" in text

    @pytest.mark.asyncio
    async def test_the_issues_section_is_still_there(self, session, admin_user):
        text = await self._report(session, admin_user)
        assert "Issues Encountered" in text

    @pytest.mark.asyncio
    async def test_the_json_export_carries_the_same_bundle(self, session, admin_user):
        import json as _json

        body = _json.loads(await self._report(session, admin_user, fmt="json"))
        entity = body["report"]["entity"] if "report" in body else body["entity"]
        assert entity["evidence"]["capabilities"]["deposit_exists"]["value"] == "yes"
        assert entity["evidence"]["code_execution"]["outcome"] == "ran_output_diverges"
        assert entity["issues"]
