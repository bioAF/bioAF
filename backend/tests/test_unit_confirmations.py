"""plan_8_3 stage 5 and section 0.4: the control that resolves an unestablished biological unit.

Stage 5 refuses a unit identity nothing the row cites states, which is right: study 56's records name
no clone, and a paper's clones cannot be read off a column's name or its order. The first
implementation shipped that refusal and recorded its control as "not built, on purpose", so the only
outcome the gate could ever reach was unresolved. A gate with no control is a dead end.

A person may record which biological unit each column came from, with the evidence it rests on. It is
deterministic (no model call, so it stays available when provider access does not), it is disclosed as
assistance, and it is not a way around the gate: a unit that is only a TYPE, or that is the column's
own name or its trailing digits, is refused here as well.
"""

import json
import pathlib

import pytest
import pytest_asyncio

from app.models.audit_log import AuditLog
from app.services.validation_unit_confirmations import (
    VERSION,
    ConfirmationRefused,
    confirmation_entry,
    record_confirmation,
)
from tests.replay import replay_mapping, restore

_SAMD1 = pathlib.Path(__file__).parent / "fixtures" / "samd1" / "study_56_persisted.json"

# What study 56 needs: its paper names the knockout clones, and its repository records do not.
_CLONES = {
    "KO Cl5 repl1": "SAMD1 KO clone Cl5",
    "KO Cl5 repl2": "SAMD1 KO clone Cl5",
    "KO Cl16": "SAMD1 KO clone Cl16",
    "KO Cl33": "SAMD1 KO clone Cl33",
}
_NOTE = "the paper's methods name three independent knockout clones, Cl5, Cl16 and Cl33, and Cl5 was sequenced twice"


def _columns() -> list[str]:
    bundle = json.loads(_SAMD1.read_text(encoding="utf-8"))
    return list(bundle["study"]["evidence_json"]["deposit_inspection"]["columns"])


class TestAConfirmationRecordsWhatAPersonSuppliedAndItsProvenance:
    def test_it_keeps_the_units_the_note_and_who_recorded_it(self):
        entry = confirmation_entry(
            matrix="counts.txt",
            units=_CLONES,
            columns=list(_CLONES),
            note=_NOTE,
            confirmed_by="a person",
            at="2026-09-17T00:00:00+00:00",
        )
        assert entry["version"] == VERSION
        assert entry["units"] == _CLONES
        assert entry["note"] == _NOTE
        assert entry["confirmed_by"] == "a person"

    def test_two_columns_may_come_from_one_unit(self):
        """Two libraries of one clone are two columns and one biological unit. That is the fact."""
        entry = confirmation_entry(
            matrix="counts.txt",
            units=_CLONES,
            columns=list(_CLONES),
            note=_NOTE,
            confirmed_by="a person",
            at="2026-09-17T00:00:00+00:00",
        )
        assert entry["units"]["KO Cl5 repl1"] == entry["units"]["KO Cl5 repl2"]


class TestAConfirmationIsNotAWayAroundTheGate:
    def _entry(self, units, **kw):
        return confirmation_entry(
            matrix="counts.txt",
            units=units,
            columns=[*units, "KO Cl5 repl1"],
            note=kw.pop("note", _NOTE),
            confirmed_by="a person",
            at="2026-09-17T00:00:00+00:00",
        )

    def test_it_states_the_evidence_it_rests_on(self):
        with pytest.raises(ConfirmationRefused, match="evidence"):
            self._entry({"KO Cl16": "SAMD1 KO clone Cl16"}, note="   ")

    def test_it_states_at_least_one_unit(self):
        with pytest.raises(ConfirmationRefused, match="no biological unit"):
            self._entry({})

    def test_a_unit_type_is_still_not_an_identity(self):
        """ "gingival fibroblast culture" is what kind of unit it is, whoever records it."""
        with pytest.raises(ConfirmationRefused, match="kind of unit"):
            self._entry({"KO Cl16": "knockout culture"})

    def test_the_columns_own_name_is_not_a_unit_identity(self):
        with pytest.raises(ConfirmationRefused, match="never read off"):
            self._entry({"KO Cl16": "KO Cl16"})

    def test_a_trailing_number_taken_off_the_column_is_not_a_unit_identity(self):
        with pytest.raises(ConfirmationRefused, match="never read off"):
            self._entry({"KO Cl5 repl1": "1"})

    def test_a_column_the_input_does_not_hold_is_refused(self):
        with pytest.raises(ConfirmationRefused, match="does not hold"):
            confirmation_entry(
                matrix="counts.txt",
                units={"KO Cl99": "SAMD1 KO clone Cl99"},
                columns=list(_CLONES),
                note=_NOTE,
                confirmed_by="a person",
                at="2026-09-17T00:00:00+00:00",
            )

    def test_a_blank_unit_is_refused(self):
        with pytest.raises(ConfirmationRefused, match="no biological unit"):
            self._entry({"KO Cl16": "  "})


class TestRecordingOneMakesTheStudysHeldMappingResolvable:
    """The control, end to end on the study that needs it. Study 56's mapping is held because nothing
    it cites states which clone each KO column came from; with the clones recorded, the mapping is
    accepted and says it was assisted."""

    @pytest.mark.asyncio
    async def test_the_held_mapping_is_accepted_and_discloses_the_assistance(self, session, admin_user):
        restored = await restore(session, 56, organization_id=admin_user.organization_id, user_id=admin_user.id)
        held = await replay_mapping(session, restored)
        assert held["status"] == "unresolved"

        entry = confirmation_entry(
            matrix=restored.evidence["input_choice"]["primary_matrix"],
            units=_CLONES,
            columns=_columns(),
            note=_NOTE,
            confirmed_by="a person",
            at="2026-09-17T00:00:00+00:00",
        )
        await record_confirmation(session, restored.study, restored.plan, entry, reason="a person recorded the clones")

        validation = await replay_mapping(session, restored)
        assert validation["status"] == "accepted"
        assert validation["assistance"] == "unit_identity_confirmed"
        assert restored.study.evidence_json["mapping_assistance"]["confirmed_by"] == "a person"

    @pytest.mark.asyncio
    async def test_an_earlier_confirmation_is_kept_when_a_later_one_replaces_it(self, session, admin_user):
        restored = await restore(session, 56, organization_id=admin_user.organization_id, user_id=admin_user.id)
        first = confirmation_entry(
            matrix="m",
            units={"KO Cl16": "SAMD1 KO clone Cl16"},
            columns=_columns(),
            note=_NOTE,
            confirmed_by="a person",
            at="2026-09-17T00:00:00+00:00",
        )
        second = confirmation_entry(
            matrix="m",
            units=_CLONES,
            columns=_columns(),
            note=_NOTE,
            confirmed_by="another person",
            at="2026-09-17T01:00:00+00:00",
        )
        await record_confirmation(session, restored.study, restored.plan, first, reason="first")
        await record_confirmation(session, restored.study, restored.plan, second, reason="second")
        current = restored.study.evidence_json["unit_confirmations"]
        assert current["units"] == _CLONES
        assert [e["confirmed_by"] for e in current["superseded"]] == ["a person"]


class TestTheApi:
    @pytest_asyncio.fixture(autouse=True)
    async def _enable(self, session):
        from app.services import beta_features_service

        await beta_features_service.set_flag(session, "lit_validation", True)
        await session.commit()

    def _body(self, **overrides):
        return {"units": dict(_CLONES), "note": _NOTE, **overrides}

    async def _study(self, session, admin_user):
        restored = await restore(session, 56, organization_id=admin_user.organization_id, user_id=admin_user.id)
        await session.commit()
        return restored.study

    @pytest.mark.asyncio
    async def test_a_requester_records_the_units_and_it_is_audited(self, session, admin_user, admin_token, client):
        study = await self._study(session, admin_user)
        headers = {"Authorization": f"Bearer {admin_token}"}
        r = await client.post(
            f"/api/validation-studies/{study.id}/unit-confirmations", json=self._body(), headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["confirmation"]["units"] == _CLONES
        await session.refresh(study)
        stored = study.evidence_json["unit_confirmations"]
        assert stored["confirmed_by"] and stored["note"] == _NOTE
        assert stored["matrix"] == study.evidence_json["input_choice"]["primary_matrix"]
        audit = (
            await session.execute(
                AuditLog.__table__.select().where(
                    AuditLog.entity_type == "validation_study",
                    AuditLog.entity_id == study.id,
                    AuditLog.action == "unit_confirmation",
                )
            )
        ).all()
        assert len(audit) == 1

    @pytest.mark.asyncio
    async def test_a_viewer_may_not_record_one(self, session, admin_user, viewer_token, client):
        study = await self._study(session, admin_user)
        headers = {"Authorization": f"Bearer {viewer_token}"}
        r = await client.post(
            f"/api/validation-studies/{study.id}/unit-confirmations", json=self._body(), headers=headers
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "overrides",
        [
            {"note": ""},
            {"units": {"a column this input does not hold": "clone Cl5"}},
            {"units": {"KO Cl16": "knockout culture"}},
            {"units": {"KO Cl16": "KO Cl16"}},
        ],
    )
    async def test_a_confirmation_that_establishes_nothing_is_refused(
        self, session, admin_user, admin_token, client, overrides
    ):
        study = await self._study(session, admin_user)
        headers = {"Authorization": f"Bearer {admin_token}"}
        r = await client.post(
            f"/api/validation-studies/{study.id}/unit-confirmations", json=self._body(**overrides), headers=headers
        )
        assert r.status_code == 422
        await session.refresh(study)
        assert "unit_confirmations" not in (study.evidence_json or {})


class TestTheReportOffersTheControlWhereTheUnitsAreWhatIsHolding:
    """plan_8_3 section 0.1: a refusal names its own way out. The report says which columns need a
    unit, which it already holds, and who recorded them."""

    @pytest.mark.asyncio
    async def test_it_names_the_columns_a_confirmation_would_resolve(self, session, admin_user):
        from tests.replay import replay_report

        restored = await restore(session, 56, organization_id=admin_user.organization_id, user_id=admin_user.id)
        await replay_mapping(session, restored)
        report = await replay_report(session, restored)
        offer = report["unit_confirmation"]
        assert offer["unresolved"] == sorted(_CLONES)
        assert offer["columns"] == _columns()
        assert offer["recorded"] is None

    @pytest.mark.asyncio
    async def test_it_shows_what_was_recorded_once_it_is(self, session, admin_user):
        from tests.replay import replay_report

        restored = await restore(session, 56, organization_id=admin_user.organization_id, user_id=admin_user.id)
        await replay_mapping(session, restored)
        entry = confirmation_entry(
            matrix=restored.evidence["input_choice"]["primary_matrix"],
            units=_CLONES,
            columns=_columns(),
            note=_NOTE,
            confirmed_by="a person",
            at="2026-09-17T00:00:00+00:00",
        )
        await record_confirmation(session, restored.study, restored.plan, entry, reason="recorded")
        await replay_mapping(session, restored)
        report = await replay_report(session, restored)
        offer = report["unit_confirmation"]
        assert offer["recorded"]["units"] == _CLONES
        assert offer["recorded"]["confirmed_by"] == "a person"
        assert offer["unresolved"] == []

    @pytest.mark.asyncio
    async def test_a_study_with_no_mapping_of_its_own_is_offered_nothing(self, session, admin_user):
        from tests.replay import replay_report

        restored = await restore(session, 55, organization_id=admin_user.organization_id, user_id=admin_user.id)
        report = await replay_report(session, restored)
        assert report["unit_confirmation"] is None
