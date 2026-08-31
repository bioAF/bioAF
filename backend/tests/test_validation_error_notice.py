"""A validation study that stops on infrastructure has to say so, and say when its data expires.

`error` means the infrastructure failed, not the paper, and the way back is a human clicking Retry.
That only works if a human is told. The validation feature called the notification service zero
times: the only account of a stopped study was a badge on a page nobody had open.

The same write stamps when the study stopped and when its fetched data becomes reapable, so the
retention window is decided once, on the server, rather than duplicated in the frontend.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.event_types import EVENT_SEVERITY, USER_CONFIGURABLE_EVENT_TYPES, VALIDATION_STUDY_ERROR
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import (
    VALIDATION_FETCH_RETENTION_DAYS,
    ValidationStudyService,
)


async def _study(session, admin_user, state: str = "requested"):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1234/x"
    )
    study.state = state
    await session.flush()
    return study


def _emitted(emit_mock, event_type: str) -> list[dict]:
    return [c.args[1] for c in emit_mock.call_args_list if c.args and c.args[0] == event_type]


class TestTheStoppedStudyAnnouncesItself:
    @pytest.mark.asyncio
    async def test_entering_error_notifies_the_person_who_asked_for_the_study(self, session, admin_user):
        study = await _study(session, admin_user, "running")
        emit = AsyncMock()

        with patch("app.services.validation_study_service.event_bus.emit", emit):
            await ValidationStudyService.transition(
                session,
                study.id,
                admin_user.organization_id,
                admin_user.id,
                "error",
                failure_reason="analysis run failed",
            )

        payloads = _emitted(emit, VALIDATION_STUDY_ERROR)
        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["org_id"] == admin_user.organization_id
        assert payload["target_user_id"] == study.requested_by_user_id
        assert payload["entity_type"] == "validation_study"
        assert payload["entity_id"] == study.id
        assert "analysis run failed" in payload["message"]

    @pytest.mark.asyncio
    async def test_the_driver_s_own_error_path_announces_it_too(self, session, admin_user):
        """`_mark_error` sets the state directly, because the handler that raised may have left an
        illegal transition behind. It is still a study stopping and still needs to say so."""
        study = await _study(session, admin_user, "running")
        emit = AsyncMock()

        with patch("app.services.validation_study_service.event_bus.emit", emit):
            await ValidationDriverService._mark_error(session, study.id, "kubernetes went away")

        payloads = _emitted(emit, VALIDATION_STUDY_ERROR)
        assert len(payloads) == 1
        assert payloads[0]["entity_id"] == study.id

    @pytest.mark.asyncio
    async def test_reaching_any_other_state_announces_nothing(self, session, admin_user):
        """A study advancing normally is not news."""
        study = await _study(session, admin_user, "setup")
        emit = AsyncMock()

        with patch("app.services.validation_study_service.event_bus.emit", emit):
            await ValidationStudyService.transition(
                session, study.id, admin_user.organization_id, admin_user.id, "running"
            )

        assert _emitted(emit, VALIDATION_STUDY_ERROR) == []

    @pytest.mark.asyncio
    async def test_a_failed_notification_never_costs_the_study_its_error_state(self, session, admin_user):
        """Recording that a study stopped is the point; telling someone is best-effort on top of it."""
        study = await _study(session, admin_user, "running")

        with patch(
            "app.services.validation_study_service.event_bus.emit",
            AsyncMock(side_effect=RuntimeError("bell is down")),
        ):
            updated = await ValidationStudyService.transition(
                session, study.id, admin_user.organization_id, admin_user.id, "error", failure_reason="boom"
            )

        assert updated.state == "error"


class TestTheDataHasADeadline:
    @pytest.mark.asyncio
    async def test_stopping_stamps_when_it_stopped_and_when_the_fetch_expires(self, session, admin_user):
        study = await _study(session, admin_user, "running")
        before = datetime.now(timezone.utc)

        with patch("app.services.validation_study_service.event_bus.emit", AsyncMock()):
            updated = await ValidationStudyService.transition(
                session, study.id, admin_user.organization_id, admin_user.id, "error", failure_reason="boom"
            )

        evidence = updated.evidence_json or {}
        error_at = datetime.fromisoformat(evidence["error_at"])
        reap_after = datetime.fromisoformat(evidence["fetch_reap_after"])
        assert error_at >= before
        assert reap_after - error_at == timedelta(days=VALIDATION_FETCH_RETENTION_DAYS)

    @pytest.mark.asyncio
    async def test_the_stamp_keeps_the_evidence_the_study_already_earned(self, session, admin_user):
        """A study can reach `error` after its QC evidence is assembled; stamping must not erase it."""
        study = await _study(session, admin_user, "running")
        study.evidence_json = {"computed_metrics": {"percent_aligned": 91.2}}
        await session.flush()

        with patch("app.services.validation_study_service.event_bus.emit", AsyncMock()):
            updated = await ValidationStudyService.transition(
                session, study.id, admin_user.organization_id, admin_user.id, "error", failure_reason="boom"
            )

        assert (updated.evidence_json or {})["computed_metrics"] == {"percent_aligned": 91.2}
        assert "fetch_reap_after" in (updated.evidence_json or {})

    @pytest.mark.asyncio
    async def test_a_retry_clears_the_deadline_so_the_data_stops_expiring(self, session, admin_user):
        """Retrying is what the deadline was waiting for. A study back in flight must not carry a
        countdown that a later, unrelated stop would not refresh."""
        study = await _study(session, admin_user, "running")

        with patch("app.services.validation_study_service.event_bus.emit", AsyncMock()):
            await ValidationStudyService.transition(
                session, study.id, admin_user.organization_id, admin_user.id, "error", failure_reason="boom"
            )
        study = await ValidationStudyService.retry_study(session, study.id, admin_user.organization_id, admin_user.id)

        evidence = study.evidence_json or {}
        assert "fetch_reap_after" not in evidence
        assert "error_at" not in evidence


class TestTheEventIsAFirstClassNotification:
    def test_it_carries_a_severity_and_a_toggle(self):
        """An event with no severity defaults silently, and one with no toggle cannot be turned off."""
        assert EVENT_SEVERITY[VALIDATION_STUDY_ERROR] == "warning"
        assert VALIDATION_STUDY_ERROR in USER_CONFIGURABLE_EVENT_TYPES
