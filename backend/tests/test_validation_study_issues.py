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
