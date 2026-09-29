"""plan_8_7 stage 3: the settings the request records are the settings the job runs under.

    "Record both the requested and effective settings; refuse an unsupported execution specification
    rather than recording unenforced settings as if applied. Verify limits on the generated job and
    the running supported adapter, not only in the request helper."

`LIMITS` was a dictionary on the request. `execute_fetched_code` was called with no resource profile
and no timeout, so it took the service defaults, and the report stated a cpu, a memory ceiling, a
timeout and a network policy that nothing had applied.
"""

import pytest

from app.services.validation_environment_check import (
    EnvironmentCheckRefused,
    effective_settings,
    request_environment_check,
)
from app.services.validation_study_service import ValidationStudyService

_SOURCE = {"path": "analysis.py", "language": "python", "text": "import numpy\nprint(numpy.mean([1]))\n"}
_MANIFEST = {"path": "requirements.txt", "text": "numpy==1.26.4\n"}


class _Compute:
    def __init__(self, **kw):
        self.id = 11
        self.status = "pending"
        self.resource_profile = kw.get("resource_profile", "small")
        self.cpu_cores = kw.get("cpu_cores", 2)
        self.memory_gb = kw.get("memory_gb", 8)


class _Storage:
    def build_uri(self, bucket, path):
        return f"gs://{bucket}/{path}"

    async def write_bytes(self, uri, payload, content_type=None):
        self.written = (uri, len(payload), content_type)


class _Identity:
    namespace = "bioaf-untrusted"
    sa_email = "bioaf-untrusted-runner@example.iam.gserviceaccount.com"
    bucket = "bioaf-untrusted"

    def prefix_for(self, study_id):
        return f"studies/{study_id}"


async def _study(session, admin_user, *, sources=(_SOURCE,), manifests=(_MANIFEST,)):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE1", intended_route="assessment"
    )
    study.evidence_json = {"code_inspection": {"sources": list(sources), "manifests": list(manifests)}}
    await session.flush()
    return study


class TestTheEffectiveSettingsAreReadBack:
    def test_a_matching_profile_without_an_enforced_contract_is_not_reported_as_applied(self):
        found = effective_settings(
            request={"resource_profile": "small", "limits": {"cpu": "2", "memory": "8Gi", "timeout_seconds": 900}},
            compute=_Compute(),
            timeout_seconds=900,
        )
        assert found["applied"] is False
        assert "no enforced execution contract" in found["reason"]
        assert found["cpu"] == "2" and found["memory"] == "8Gi"
        assert found["timeout_seconds"] == 900

    def test_a_profile_the_executor_changed_is_reported_as_not_applied(self):
        found = effective_settings(
            request={"resource_profile": "small", "limits": {"cpu": "2", "memory": "8Gi", "timeout_seconds": 900}},
            compute=_Compute(resource_profile="large", cpu_cores=8, memory_gb=32),
            timeout_seconds=900,
        )
        assert found["applied"] is False
        assert found["cpu"] == "8"
        assert "requested" in found["reason"]

    def test_a_timeout_the_executor_shortened_is_reported_as_not_applied(self):
        found = effective_settings(
            request={"resource_profile": "small", "limits": {"cpu": "2", "memory": "8Gi", "timeout_seconds": 900}},
            compute=_Compute(),
            timeout_seconds=60,
        )
        assert found["applied"] is False
        assert found["timeout_seconds"] == 60


@pytest.mark.asyncio
class TestTheRequestReachesTheExecutor:
    async def test_the_resource_profile_and_timeout_are_passed(self, session, admin_user, monkeypatch):
        study = await _study(session, admin_user)
        passed = {}

        async def _execute(session, **kw):
            passed.update(kw)
            return _Compute()

        monkeypatch.setattr(
            "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code", _execute
        )
        monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", _identity)
        monkeypatch.setattr("app.services.validation_environment_check.get_storage_adapter", lambda: _Storage())
        record = await request_environment_check(session, study, user_id=admin_user.id)
        assert passed["resource_profile"] == "small"
        assert passed["timeout_seconds"] == 900
        assert record["status"] == "running"

    async def test_the_record_carries_both_the_requested_and_the_effective_settings(
        self, session, admin_user, monkeypatch
    ):
        study = await _study(session, admin_user)

        async def _execute(session, **kw):
            return _Compute(resource_profile="large", cpu_cores=8, memory_gb=32)

        monkeypatch.setattr(
            "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code", _execute
        )
        monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", _identity)
        monkeypatch.setattr("app.services.validation_environment_check.get_storage_adapter", lambda: _Storage())
        record = await request_environment_check(session, study, user_id=admin_user.id)
        assert record["limits"]["cpu"] == "2", "what was asked for"
        assert record["effective"]["cpu"] == "8", "what the job actually got"
        assert record["effective"]["applied"] is False

    async def test_the_source_revision_it_ran_against_is_recorded(self, session, admin_user, monkeypatch):
        study = await _study(session, admin_user)

        async def _execute(session, **kw):
            return _Compute()

        monkeypatch.setattr(
            "app.services.notebook_execution_service.NotebookExecutionService.execute_fetched_code", _execute
        )
        monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", _identity)
        monkeypatch.setattr("app.services.validation_environment_check.get_storage_adapter", lambda: _Storage())
        record = await request_environment_check(session, study, user_id=admin_user.id)
        assert record["source_digest"]
        assert record["manifest_digest"]

    async def test_no_isolated_identity_refuses_rather_than_borrowing_one(self, session, admin_user, monkeypatch):
        study = await _study(session, admin_user)

        async def _none(session):
            return None

        monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", _none)
        with pytest.raises(EnvironmentCheckRefused):
            await request_environment_check(session, study, user_id=admin_user.id)

    async def test_an_unsupported_runtime_refuses_before_anything_is_staged(self, session, admin_user, monkeypatch):
        study = await _study(session, admin_user, sources=[{"path": "a.jl", "language": "julia", "text": "1"}])
        monkeypatch.setattr("app.services.untrusted_execution.untrusted_identity", _identity)
        with pytest.raises(EnvironmentCheckRefused):
            await request_environment_check(session, study, user_id=admin_user.id)


async def _identity(session):
    return _Identity()
