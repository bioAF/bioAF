"""plan_8_7 stage 3: the follow-up is scheduled by the normal Validate workflow, not by a toggle.

"Under existing authorization, discovery of runnable code and compatible inputs schedules the
actual author-code attempt. An impediment to full reproduction triggers the meaningful bounded run
check where feasible, with exact limitations. If all execution is blocked, publish its cause and
preserve the documentary report. Test these branches through production callers, including a user
whose existing authorization covers the attempt without another route-selection step."
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.validation_assessment import schedule_code_followup
from app.services.validation_study_service import ValidationStudyService


@pytest.fixture(autouse=True)
def isolated_executor(monkeypatch):
    identity = SimpleNamespace(bucket="isolated", prefix_for=lambda study_id: f"studies/{study_id}")
    storage = SimpleNamespace(build_uri=lambda bucket, path: f"gs://{bucket}/{path}", write_bytes=AsyncMock())
    monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: storage)
    compute = SimpleNamespace(id=1, status="running", provider_metadata={})
    monkeypatch.setattr("app.services.validation_environment_check._compute_session", AsyncMock(return_value=compute))
    monkeypatch.setattr(
        "app.services.notebook_execution_service.NotebookExecutionService.poll_execution",
        AsyncMock(return_value=compute),
    )


_PY = {"path": "analysis.py", "language": "python", "text": "import numpy\nprint(numpy.mean([1]))\n"}
_JULIA = {"path": "sim.jl", "language": "julia", "text": "println(1)"}


async def _study(session, admin_user, *, route, sources=(_PY,), processed="yes"):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route=route
    )
    study.evidence_json = {
        "code_inspection": {
            "sources": list(sources),
            "manifests": [{"path": "requirements.txt", "text": "numpy==1.26.4\n"}],
        },
        "code_resolution": {"outcome": "resolved", "url": "https://github.com/x/y", "commit_sha": "abc"},
        "capabilities": {"preprocessed_data": {"value": processed}},
    }
    await session.flush()
    return study


@pytest.mark.asyncio
class TestTheFollowUpIsRecordedByTheAssessment:
    async def test_it_lands_on_the_evidence_with_one_row_per_implementation(self, session, admin_user):
        study = await _study(session, admin_user, route="assessment")
        found = await schedule_code_followup(session, study)
        assert [row["unit"] for row in found["followups"]] == ["code:analysis.py"]
        assert study.evidence_json["code_followup"]["followups"]

    async def test_an_assessment_only_study_spends_nothing_and_names_the_authorization(
        self, session, admin_user, monkeypatch
    ):
        study = await _study(session, admin_user, route="assessment")
        launched = []
        monkeypatch.setattr(
            "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code",
            _record(launched),
        )
        found = await schedule_code_followup(session, study)
        assert launched == []
        assert found["followups"][0]["action"] == "needs_authorization"

    async def test_an_authorized_study_with_no_inputs_launches_the_bounded_check(
        self, session, admin_user, monkeypatch
    ):
        study = await _study(session, admin_user, route="deposit", processed="no")
        launched = []
        monkeypatch.setattr(
            "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code",
            _record(launched),
        )
        found = await schedule_code_followup(session, study)
        assert len(launched) == 1
        assert found["scheduled"] == [found["followups"][0]["operation_id"]]
        assert found["followups"][0]["session_id"] == 1

    async def test_a_blocked_runtime_publishes_its_cause_and_launches_nothing(self, session, admin_user, monkeypatch):
        study = await _study(session, admin_user, route="deposit", sources=[_JULIA])
        launched = []
        monkeypatch.setattr(
            "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code",
            _record(launched),
        )
        found = await schedule_code_followup(session, study)
        assert launched == []
        assert found["followups"][0]["action"] == "blocked"
        assert found["followups"][0]["missing"] == "runtime"

    async def test_a_refused_check_is_recorded_rather_than_raised(self, session, admin_user, monkeypatch):
        from app.services.validation_environment_check import EnvironmentCheckRefused

        study = await _study(session, admin_user, route="deposit", processed="no")

        async def _refuse(session, **kwargs):
            raise EnvironmentCheckRefused("this install has no isolated identity for untrusted code")

        monkeypatch.setattr(
            "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code", _refuse
        )
        found = await schedule_code_followup(session, study)
        assert "isolated identity" in found["blocked"][0]["reason"]
        assert found["followups"], "the documentary follow-up survives a refused execution"

    async def test_running_it_again_does_not_launch_a_second_check(self, session, admin_user, monkeypatch):
        study = await _study(session, admin_user, route="deposit", processed="no")
        launched = []
        monkeypatch.setattr(
            "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code",
            _record(launched),
        )
        await schedule_code_followup(session, study)
        await schedule_code_followup(session, study)
        assert len(launched) == 1


def _record(launched):
    async def _launch(session, **kwargs):
        launched.append(kwargs)
        assert kwargs["entry_point"] == "analysis.py"
        assert kwargs["operation_key"]
        return SimpleNamespace(id=1, status="running", provider_metadata={})

    return _launch


@pytest.mark.asyncio
async def test_the_assessment_stage_records_the_follow_up(session, admin_user, monkeypatch):
    from app.services import validation_assessment

    study = await _study(session, admin_user, route="assessment")
    called = []

    async def _schedule(session, study, *, claim=None):
        called.append(study.id)
        return {"followups": []}

    monkeypatch.setattr(validation_assessment, "schedule_code_followup", _schedule)
    await validation_assessment.run_assessment(session, study)
    assert called == [study.id]
