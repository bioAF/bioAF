"""plan_8_5 section 3.5: a person approves an isolated environment check, and its result settles
C1.B and C2.B or nothing at all.

The identity this runs under is the one plan_7 step 16a built for code fetched from a paper's
authors: its own namespace, no project-level role, one bucket. There is no fallback, so an install
without it refuses rather than borrowing the notebook runner's credential.
"""

import pytest
import pytest_asyncio
from types import SimpleNamespace

from app.services.validation_environment_check import (
    EnvironmentCheckRefused,
    request_environment_check,
    settle_environment_check,
)
from app.services.validation_study_service import ValidationStudyService

_SOURCES = [{"path": "analysis.R", "language": "r", "text": "library(DESeq2)\nprint(1)\n"}]


async def _study(session, admin_user, sources=None):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1/env", intended_route="deposit"
    )
    study.evidence_json = {"code_inspection": {"sources": sources if sources is not None else _SOURCES}}
    await session.flush()
    return study


class _Identity:
    namespace = "bioaf-untrusted"
    ksa = "bioaf-untrusted-runner"
    sa_email = "runner@example.iam.gserviceaccount.com"
    bucket = "untrusted-bucket"

    def prefix_for(self, study_id):
        return f"study-{study_id}"


class _Storage:
    def __init__(self):
        self.written = {}

    def build_uri(self, bucket, path):
        return f"gs://{bucket}/{path}"

    async def write_text(self, uri, text, content_type=None):
        self.written[uri] = text


def _patch(monkeypatch, *, identity=_Identity(), storage=None, submitted=None):
    storage = storage or _Storage()

    async def _identity(session):
        return identity

    def _storage():
        return storage

    async def _execute(session, **kw):
        if submitted is not None:
            submitted.update(kw)
        return SimpleNamespace(id=77)

    monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", _identity)
    monkeypatch.setattr("app.services.validation_environment_check.get_storage_adapter", _storage)
    monkeypatch.setattr(
        "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code", _execute
    )
    return storage


class TestRequestingOne:
    @pytest.mark.asyncio
    async def test_it_stages_the_source_and_submits_it_under_the_isolated_identity(
        self, session, admin_user, monkeypatch
    ):
        submitted = {}
        storage = _patch(monkeypatch, submitted=submitted)
        study = await _study(session, admin_user)
        record = await request_environment_check(session, study, user_id=admin_user.id)
        assert record["session_id"] == 77
        assert submitted["entry_point"] == "bioaf_environment_check.R"
        assert any("analysis.R" in uri for uri in storage.written)
        assert record["status"] == "running"

    @pytest.mark.asyncio
    async def test_the_request_is_recorded_on_the_study_with_what_it_would_settle(
        self, session, admin_user, monkeypatch
    ):
        _patch(monkeypatch)
        study = await _study(session, admin_user)
        await request_environment_check(session, study, user_id=admin_user.id)
        held = study.evidence_json["code_inspection"]["environment_check"]
        assert held["establishes"] == ["C1.B", "C2.B"]
        assert held["language"] == "r"
        assert held["at"]

    @pytest.mark.asyncio
    async def test_an_install_with_no_isolated_identity_refuses_rather_than_borrowing_one(
        self, session, admin_user, monkeypatch
    ):
        _patch(monkeypatch, identity=None)
        study = await _study(session, admin_user)
        with pytest.raises(EnvironmentCheckRefused) as refusal:
            await request_environment_check(session, study, user_id=admin_user.id)
        assert "isolated identity" in str(refusal.value)

    @pytest.mark.asyncio
    async def test_a_study_with_no_source_has_nothing_to_check(self, session, admin_user, monkeypatch):
        _patch(monkeypatch)
        study = await _study(session, admin_user, sources=[])
        with pytest.raises(EnvironmentCheckRefused):
            await request_environment_check(session, study, user_id=admin_user.id)


class TestSettlingOne:
    def _session(self, status, transcript, exit_code=0):
        held = SimpleNamespace(id=77, status=status, output_log=transcript, failure_message=None, exit_code=exit_code)

        async def _get(session_, session_id):
            return held

        return _get

    @pytest.mark.asyncio
    async def test_a_clean_run_verifies_both_obligations_on_the_score(self, session, admin_user, monkeypatch):
        _patch(monkeypatch)
        study = await _study(session, admin_user)
        await request_environment_check(session, study, user_id=admin_user.id)

        async def _poll(session_, cs):
            return cs

        monkeypatch.setattr("app.services.notebook_execution_service.NotebookExecutionService.poll_execution", _poll)
        monkeypatch.setattr(
            "app.services.validation_environment_check._compute_session",
            self._session("completed", "BIOAF_LOAD DESeq2 ok\nBIOAF_RESOLVE ok\n"),
        )
        await settle_environment_check(session, study)
        execution = study.evidence_json["code_inspection"]["execution"]
        assert execution["load"]["status"] == "succeeded"
        assert execution["dependency_resolution"]["status"] == "succeeded"
        from app.services.validation_code_checks import assess_code

        assessed = assess_code(sources=_SOURCES, execution=execution)
        assert assessed["C1.B"]["outcome"] == "verified"
        assert assessed["C2.B"]["outcome"] == "verified"

    @pytest.mark.asyncio
    async def test_a_package_that_will_not_load_is_a_named_failure(self, session, admin_user, monkeypatch):
        _patch(monkeypatch)
        study = await _study(session, admin_user)
        await request_environment_check(session, study, user_id=admin_user.id)

        async def _poll(session_, cs):
            return cs

        monkeypatch.setattr("app.services.notebook_execution_service.NotebookExecutionService.poll_execution", _poll)
        monkeypatch.setattr(
            "app.services.validation_environment_check._compute_session",
            self._session("failed", "BIOAF_LOAD DESeq2 failed: not installed\n", exit_code=1),
        )
        await settle_environment_check(session, study)
        execution = study.evidence_json["code_inspection"]["execution"]
        assert execution["load"]["status"] == "failed"
        assert "DESeq2" in execution["load"]["reason"]

    @pytest.mark.asyncio
    async def test_a_run_that_said_nothing_settles_nothing(self, session, admin_user, monkeypatch):
        _patch(monkeypatch)
        study = await _study(session, admin_user)
        await request_environment_check(session, study, user_id=admin_user.id)

        async def _poll(session_, cs):
            return cs

        monkeypatch.setattr("app.services.notebook_execution_service.NotebookExecutionService.poll_execution", _poll)
        monkeypatch.setattr(
            "app.services.validation_environment_check._compute_session",
            self._session("failed", "", exit_code=137),
        )
        await settle_environment_check(session, study)
        execution = (study.evidence_json["code_inspection"] or {}).get("execution") or {}
        assert "load" not in execution
        from app.services.validation_code_checks import assess_code

        assert assess_code(sources=_SOURCES, execution=execution)["C1.B"]["outcome"] == "undetermined"

    @pytest.mark.asyncio
    async def test_a_run_still_going_settles_nothing_yet(self, session, admin_user, monkeypatch):
        _patch(monkeypatch)
        study = await _study(session, admin_user)
        await request_environment_check(session, study, user_id=admin_user.id)

        async def _poll(session_, cs):
            return cs

        monkeypatch.setattr("app.services.notebook_execution_service.NotebookExecutionService.poll_execution", _poll)
        monkeypatch.setattr(
            "app.services.validation_environment_check._compute_session",
            self._session("running", "BIOAF_LOAD DESeq2 ok\n"),
        )
        await settle_environment_check(session, study)
        assert study.evidence_json["code_inspection"]["environment_check"]["status"] == "running"
        assert "execution" not in study.evidence_json["code_inspection"]


class TestTheControlAPersonUses:
    """plan_8_5 section 3.5: the approval is a person asking for it, through the study's own API."""

    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    @pytest.mark.asyncio
    async def test_a_person_can_request_one_and_gets_what_it_would_settle(
        self, client, session, admin_user, admin_token, monkeypatch
    ):
        _patch(monkeypatch)
        study = await _study(session, admin_user)
        await session.commit()
        response = await client.post(
            f"/api/validation-studies/{study.id}/environment-check",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["environment_check"]["establishes"] == ["C1.B", "C2.B"]

    @pytest.mark.asyncio
    async def test_an_install_with_no_isolated_identity_says_so_rather_than_failing_obscurely(
        self, client, session, admin_user, admin_token, monkeypatch
    ):
        _patch(monkeypatch, identity=None)
        study = await _study(session, admin_user)
        await session.commit()
        response = await client.post(
            f"/api/validation-studies/{study.id}/environment-check",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 409
        assert "isolated identity" in response.json()["detail"]
