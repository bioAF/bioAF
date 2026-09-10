"""change_7.2 section 7: record what BUILD produced each stage, and say "unknown" where it cannot.

Studies 32 and 33 both reported version `2026.9.1`. The backend image was rebuilt at 10:20:13 UTC on
2026-09-09 and the container started at 10:24:47; study 32 ran at 09:17 on the previous container.
The version tag is mutable, so the two exports establish that observed behaviour differed and cannot
attribute the difference to code.

Two observability defects made that investigation slower than it needed to be, and both are here:
the failed-parse path threw away the evidence needed to attribute a failure, and a capped answer was
reported as an unparseable one.
"""

import pytest

from app.services import validation_provenance as prov
from app.services.llm_decision import OUTCOME_TRUNCATED, OUTCOMES, _ERROR_CLASS_OUTCOMES
from app.services.validation_study_service import ValidationStudyService


class TestProvenanceCanSayUnknown:
    def test_an_unstamped_build_reports_unknown_rather_than_guessing(self):
        build = prov.current_build()
        assert build["commit"] == prov.UNKNOWN or build["commit"]
        assert set(build) == {"commit", "image_digest", "version"}

    @pytest.mark.asyncio
    async def test_a_study_with_no_records_says_its_stages_are_unknown(self, session, admin_user):
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        summary = prov.provenance_summary(study)
        assert summary["stages_before_recording"] is True
        assert summary["stages"] == []

    @pytest.mark.asyncio
    async def test_no_old_stage_is_stamped_with_the_current_build(self, session, admin_user):
        """Stamping a stage that ran before this existed would manufacture exactly the false
        certainty that made studies 32 and 33 look comparable."""
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        study.evidence_json = {"capabilities": {}, "supplements": []}
        prov.record_stage(study, "acquiring_processed")
        stages = prov.stages_of(study)
        assert [s["stage"] for s in stages] == ["acquiring_processed"]


class TestProvenanceIsPerStage:
    @pytest.mark.asyncio
    async def test_each_stage_gets_its_own_record(self, session, admin_user):
        """A study can span deployments: study 29 has been alive across at least one rebuild."""
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        prov.record_stage(study, "reading")
        prov.record_stage(study, "acquiring_processed")
        assert [s["stage"] for s in prov.stages_of(study)] == ["reading", "acquiring_processed"]

    @pytest.mark.asyncio
    async def test_ticking_the_same_stage_does_not_write_a_row_every_thirty_seconds(self, session, admin_user):
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        prov.record_stage(study, "acquiring_processed")
        assert prov.record_stage(study, "acquiring_processed") is None
        assert len(prov.stages_of(study)) == 1

    @pytest.mark.asyncio
    async def test_a_study_spanning_two_builds_says_so(self, session, admin_user, monkeypatch):
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        monkeypatch.setattr(prov, "current_build", lambda: {"commit": "aaa", "image_digest": "d1", "version": "v1"})
        prov.record_stage(study, "reading")
        monkeypatch.setattr(prov, "current_build", lambda: {"commit": "bbb", "image_digest": "d2", "version": "v1"})
        prov.record_stage(study, "acquiring_processed")
        assert prov.provenance_summary(study)["spans_more_than_one_build"] is True

    @pytest.mark.asyncio
    async def test_the_same_stage_after_a_redeploy_is_recorded_again(self, session, admin_user, monkeypatch):
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        monkeypatch.setattr(prov, "current_build", lambda: {"commit": "aaa", "image_digest": "d1", "version": "v1"})
        prov.record_stage(study, "acquiring_processed")
        monkeypatch.setattr(prov, "current_build", lambda: {"commit": "bbb", "image_digest": "d2", "version": "v1"})
        assert prov.record_stage(study, "acquiring_processed") is not None


class TestTheReportStatesTheBuild:
    def test_a_study_with_no_provenance_is_described_as_unknown(self):
        from app.services.provenance.markdown_renderer import _append_build_provenance

        parts: list[str] = []
        _append_build_provenance(parts, {"stages": [], "stages_before_recording": True})
        assert any("unknown" in line.lower() for line in parts)

    def test_the_section_reaches_the_validation_study_report(self):
        """The renderer has six entity reports and the call landed in the wrong one, so the section
        rendered on a SAMPLE report and never on the study it describes. Asserting on the helper
        alone could not see that."""
        from app.services.provenance.markdown_renderer import MarkdownRenderer

        rendered = MarkdownRenderer.render(
            "validation_study",
            {
                "generated_at": "now",
                "generated_by": "test",
                "report_type": "validation_study",
                "schema_version": "1",
                "organization": {"name": "demo"},
                "entity": {
                    "id": 1,
                    "state": "classified",
                    "build_provenance": {
                        "stages": [{"stage": "assessment", "at": "now", "commit": "abc123", "image_digest": "sha256:x"}],
                        "spans_more_than_one_build": False,
                    },
                },
                "audit_trail": [],
            },
        )
        assert "## Build Provenance" in rendered
        assert "abc123" in rendered

    def test_a_study_with_no_stages_still_gets_the_section(self):
        from app.services.provenance.markdown_renderer import MarkdownRenderer

        rendered = MarkdownRenderer.render(
            "validation_study",
            {
                "generated_at": "now",
                "generated_by": "test",
                "report_type": "validation_study",
                "schema_version": "1",
                "organization": {"name": "demo"},
                "entity": {"id": 1, "state": "classified", "build_provenance": {"stages": []}},
                "audit_trail": [],
            },
        )
        assert "## Build Provenance" in rendered
        assert "unknown" in rendered.lower()

    def test_a_stamped_study_shows_its_commit_and_digest(self):
        from app.services.provenance.markdown_renderer import _append_build_provenance

        parts: list[str] = []
        _append_build_provenance(
            parts,
            {
                "stages": [{"stage": "reading", "at": "now", "commit": "abc123", "image_digest": "sha256:x"}],
                "spans_more_than_one_build": False,
            },
        )
        rendered = "\n".join(parts)
        assert "abc123" in rendered
        assert "sha256:x" in rendered


class TestACappedAnswerIsNotAnUnreadableOne:
    def test_truncated_is_its_own_outcome(self):
        assert OUTCOME_TRUNCATED in OUTCOMES
        assert OUTCOME_TRUNCATED != "unparseable"

    def test_a_provider_truncation_maps_to_it(self):
        assert _ERROR_CLASS_OUTCOMES["truncated"] == OUTCOME_TRUNCATED

    def test_the_anthropic_client_reports_a_token_limit_as_truncation(self):
        """The client handled `stop_reason: "refusal"` and not `"max_tokens"`, so a capped answer
        was reported as unparseable: a true statement about the text and a false one about what
        happened."""
        import asyncio

        import httpx

        from app.services.llm_provider_clients import ProviderError, anthropic_client

        class _Resp:
            status_code = 200
            content = b"{}"
            text = ""

            @staticmethod
            def json():
                return {"content": [{"type": "text", "text": '{"partial": '}], "stop_reason": "max_tokens"}

        async def _fake_request(sender, what=""):
            return _Resp()

        original = anthropic_client.request_with_retry
        anthropic_client.request_with_retry = _fake_request
        try:
            with pytest.raises(ProviderError) as ei:
                asyncio.get_event_loop().run_until_complete(
                    anthropic_client.submit("p", "payload", "claude-opus-5", "key")
                ) if False else asyncio.run(
                    anthropic_client.submit("p", "payload", "claude-opus-5", "key")
                )
        finally:
            anthropic_client.request_with_retry = original
        assert ei.value.error_class == "truncated"
        assert "cut off" in str(ei.value)
        assert isinstance(httpx.Timeout(1.0), httpx.Timeout)

    def test_the_reason_a_reader_sees_names_the_token_limit(self):
        from app.services.llm_decision import _failure_reason

        message = _failure_reason(OUTCOME_TRUNCATED, intent="binding the paper's claims", model="claude-opus-5")
        assert "cut off" in message
        assert "token limit" in message


class TestTheEvidenceForAFailedParseSurvives:
    def test_the_whole_answer_reaches_the_decision(self):
        """400 characters is not enough to attribute a failure to a cause, and no unparseable
        response in either of the owner's runs could be explained afterwards."""
        import asyncio

        from app.services.llm_decision import decide

        long_answer = "prose, and rather a lot of it. " * 100

        class _Client:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return long_answer

        decision = asyncio.run(
            decide(intent="binding the paper's claims", system="s", payload="p", client=_Client(), model="m", api_key=None)
        )
        assert decision.outcome == "unparseable"
        assert len(decision.text) == len(long_answer)

    def test_the_issue_a_user_reads_stays_plain(self):
        """The repo's rule: plain language on screen, technical detail in the logs. The raw answer
        does not go on the issue record."""
        import asyncio

        from app.services.llm_decision import decide

        class _Client:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return "a stack trace and a wall of json fragments"

        decision = asyncio.run(
            decide(intent="binding the paper's claims", system="s", payload="p", client=_Client(), model="m", api_key=None)
        )
        issue = decision.as_issue(impact="degraded")
        assert "stack trace" not in issue["message"]
