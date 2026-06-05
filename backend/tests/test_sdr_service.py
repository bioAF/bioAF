"""SDR service (ADR-063, ADR-064), Phase C acceptance criteria.

Covers numbering (AC-C01), the status machine and its guards (AC-C02, AC-C03,
AC-C04, AC-C11, AC-C13), supersession linkage (AC-C07), owner reassignment
(AC-C08), and the daily re-assessment trigger evaluation (AC-C05, AC-C06).
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.sdr import ScientificDecisionRecord, SdrStatusTransition
from app.services.sdr_service import (
    CategoryInUseError,
    InvalidTransitionError,
    SdrService,
    SupersededByRequiredError,
    TransitionNoteRequiredError,
)


async def _make_user(session, org_id, email, role_id):
    from app.models.user import User
    from app.services.auth_service import AuthService

    user = User(
        email=email,
        password_hash=AuthService.hash_password("password123"),
        organization_id=org_id,
        role_id=role_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    return user


async def _category(session, org_id, user_id, name="Analysis"):
    return await SdrService.create_category(session, org_id=org_id, user_id=user_id, name=name)


async def _make_sdr(session, org_id, user_id, **kw):
    defaults = dict(
        title="Use STARsolo over CellRanger",
        decision="Standardize on STARsolo.",
        justification="Better handling of multimapping reads.",
    )
    defaults.update(kw)
    return await SdrService.create_sdr(session, org_id=org_id, user_id=user_id, **defaults)


@pytest.mark.asyncio
async def test_create_assigns_sequential_org_scoped_number(session, admin_user):
    # AC-C01
    org_id, uid = admin_user.organization_id, admin_user.id
    first = await _make_sdr(session, org_id, uid)
    second = await _make_sdr(session, org_id, uid, title="Viability threshold 70%")
    assert first.sdr_number == 1
    assert second.sdr_number == 2
    assert first.status == "draft"
    assert first.owner_user_id == uid
    assert "created" in await _audit_actions(session, "sdr", first.id)


@pytest.mark.asyncio
async def test_numbering_is_per_org(session, admin_user):
    # AC-C01: a second org starts its own sequence at 1
    from app.models.organization import Organization

    org_id, uid = admin_user.organization_id, admin_user.id
    await _make_sdr(session, org_id, uid)
    from app.services.bootstrap_roles import seed_builtin_roles

    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    other_roles = await seed_builtin_roles(session, other.id)
    other_user = await _make_user(session, other.id, "owner2@test.com", other_roles["admin"])
    other_sdr = await _make_sdr(session, other.id, other_user.id)
    assert other_sdr.sdr_number == 1


@pytest.mark.asyncio
async def test_activate_draft(session, admin_user):
    # AC-C02, AC-C11
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid)
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active")
    refreshed = await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id)
    assert refreshed.status == "active"
    transitions = await _transitions(session, sdr.id)
    assert transitions[-1].from_status == "draft" and transitions[-1].to_status == "active"


@pytest.mark.asyncio
async def test_invalid_transition_rejected(session, admin_user):
    # AC-C13: superseded -> active is not a permitted transition
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid)
    with pytest.raises(InvalidTransitionError):
        await SdrService.transition(
            session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="superseded"
        )
    # draft -> superseded is also invalid (must be active/flagged first)


@pytest.mark.asyncio
async def test_flagged_to_active_requires_note(session, admin_user):
    # AC-C03
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid)
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active")
    await SdrService.transition(
        session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="flagged_for_review"
    )
    with pytest.raises(TransitionNoteRequiredError):
        await SdrService.transition(
            session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active"
        )
    # With a note it succeeds
    await SdrService.transition(
        session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active", note="Decision upheld; data still supports it."
    )
    refreshed = await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id)
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_supersede_requires_target_and_links_bidirectionally(session, admin_user):
    # AC-C04, AC-C07
    org_id, uid = admin_user.organization_id, admin_user.id
    old = await _make_sdr(session, org_id, uid)
    new = await _make_sdr(session, org_id, uid, title="STARsolo v2 config")
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=old.id, to_status="active")

    with pytest.raises(SupersededByRequiredError):
        await SdrService.transition(
            session, org_id=org_id, user_id=uid, sdr_id=old.id, to_status="superseded"
        )

    await SdrService.transition(
        session, org_id=org_id, user_id=uid, sdr_id=old.id, to_status="superseded", superseded_by_sdr_id=new.id
    )
    old_r = await SdrService.get_sdr(session, sdr_id=old.id, org_id=org_id)
    new_r = await SdrService.get_sdr(session, sdr_id=new.id, org_id=org_id)
    assert old_r.status == "superseded"
    assert old_r.superseded_by_sdr_id == new.id
    assert new_r.supersedes_sdr_id == old.id


@pytest.mark.asyncio
async def test_supersede_rejects_cross_org_or_self_target(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid)
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active")
    with pytest.raises(SupersededByRequiredError):
        await SdrService.transition(
            session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="superseded", superseded_by_sdr_id=sdr.id
        )
    with pytest.raises(SupersededByRequiredError):
        await SdrService.transition(
            session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="superseded", superseded_by_sdr_id=999999
        )


@pytest.mark.asyncio
async def test_edit_active_decision_logs_previous_value(session, admin_user):
    # F-LKC-02: editing active decision/justification records prior values
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid, decision="old decision")
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active")
    await SdrService.update_sdr(
        session, org_id=org_id, user_id=uid, sdr_id=sdr.id, decision="new decision"
    )
    refreshed = await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id)
    assert refreshed.decision == "new decision"
    notes = [t for t in await _transitions(session, sdr.id) if t.from_status == t.to_status == "active"]
    assert notes and "old decision" in (notes[-1].note or "")


@pytest.mark.asyncio
async def test_reassign_owner_audits_and_notifies(session, admin_user):
    # AC-C08
    org_id, uid = admin_user.organization_id, admin_user.id
    role_map = admin_user._test_role_map
    new_owner = await _make_user(session, org_id, "newowner@test.com", role_map["comp_bio"])
    sdr = await _make_sdr(session, org_id, uid)
    await SdrService.reassign_owner(
        session, org_id=org_id, user_id=uid, sdr_id=sdr.id, new_owner_user_id=new_owner.id
    )
    refreshed = await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id)
    assert refreshed.owner_user_id == new_owner.id
    assert "owner_reassigned" in await _audit_actions(session, "sdr", sdr.id)
    notifs = (
        await session.execute(select(Notification).where(Notification.user_id == new_owner.id))
    ).scalars().all()
    assert any(f"SDR-{sdr.sdr_number:03d}" in n.message for n in notifs)


@pytest.mark.asyncio
async def test_trigger_reached_flags_and_notifies_owner(session, admin_user):
    # AC-C05
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid, trigger_date=date(2026, 1, 1))
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active")
    result = await SdrService.evaluate_triggers(session, today=date(2026, 6, 5))
    refreshed = await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id)
    assert refreshed.status == "flagged_for_review"
    assert result["flagged"] == 1
    # System transition recorded with a system note
    last = (await _transitions(session, sdr.id))[-1]
    assert last.to_status == "flagged_for_review" and last.transitioned_by_user_id is None
    notifs = (
        await session.execute(select(Notification).where(Notification.user_id == uid))
    ).scalars().all()
    assert any("flagged for review" in n.message.lower() for n in notifs)


@pytest.mark.asyncio
async def test_seven_day_warning_sent_once(session, admin_user):
    # AC-C06
    org_id, uid = admin_user.organization_id, admin_user.id
    trigger = date(2026, 6, 10)
    sdr = await _make_sdr(session, org_id, uid, trigger_date=trigger)
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active")

    r1 = await SdrService.evaluate_triggers(session, today=date(2026, 6, 5))
    assert r1["warned"] == 1
    r2 = await SdrService.evaluate_triggers(session, today=date(2026, 6, 6))
    assert r2["warned"] == 0  # already warned, not re-sent

    warn_notifs = (
        await session.execute(
            select(Notification).where(
                Notification.user_id == uid, Notification.event_type == "sdr_reassessment_warning"
            )
        )
    ).scalars().all()
    assert len(warn_notifs) == 1
    # Still active (warning is not a status change)
    refreshed = await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id)
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_changing_trigger_date_resets_warning(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid, trigger_date=date(2026, 6, 10))
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=sdr.id, to_status="active")
    await SdrService.evaluate_triggers(session, today=date(2026, 6, 5))
    # Push the date out; warning bookkeeping resets so the next window warns again
    await SdrService.update_sdr(
        session, org_id=org_id, user_id=uid, sdr_id=sdr.id, trigger_date=date(2026, 12, 1)
    )
    refreshed = await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id)
    assert refreshed.trigger_warning_sent_at is None
    r = await SdrService.evaluate_triggers(session, today=date(2026, 11, 27))
    assert r["warned"] == 1


@pytest.mark.asyncio
async def test_list_default_hides_historical(session, admin_user):
    # AC-C10 (service-level filtering)
    org_id, uid = admin_user.organization_id, admin_user.id
    a = await _make_sdr(session, org_id, uid, title="Active one")
    b = await _make_sdr(session, org_id, uid, title="To repeal")
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=a.id, to_status="active")
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=b.id, to_status="active")
    await SdrService.transition(session, org_id=org_id, user_id=uid, sdr_id=b.id, to_status="repealed")

    default_rows, default_total = await SdrService.list_sdrs(session, org_id=org_id)
    assert default_total == 1 and default_rows[0].id == a.id
    all_rows, all_total = await SdrService.list_sdrs(session, org_id=org_id, include_historical=True)
    assert all_total == 2


@pytest.mark.asyncio
async def test_category_in_use_cannot_be_deleted(session, admin_user):
    # F-LKC-08
    org_id, uid = admin_user.organization_id, admin_user.id
    cat = await _category(session, org_id, uid, name="QC Thresholds")
    sdr = await _make_sdr(session, org_id, uid, category_id=cat.id)
    with pytest.raises(CategoryInUseError):
        await SdrService.delete_category(session, org_id=org_id, user_id=uid, category_id=cat.id)
    # Reassign and then delete works
    await SdrService.update_sdr(session, org_id=org_id, user_id=uid, sdr_id=sdr.id, category_id=None)
    assert await SdrService.delete_category(session, org_id=org_id, user_id=uid, category_id=cat.id) is True


@pytest.mark.asyncio
async def test_org_isolation_on_get(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    sdr = await _make_sdr(session, org_id, uid)
    assert await SdrService.get_sdr(session, sdr_id=sdr.id, org_id=org_id + 999) is None


# --- helpers -----------------------------------------------------------------


async def _audit_actions(session, entity_type, entity_id):
    rows = (
        await session.execute(
            select(AuditLog.action).where(
                AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id
            )
        )
    ).scalars().all()
    return list(rows)


async def _transitions(session, sdr_id):
    rows = (
        await session.execute(
            select(SdrStatusTransition)
            .where(SdrStatusTransition.sdr_id == sdr_id)
            .order_by(SdrStatusTransition.id)
        )
    ).scalars().all()
    return list(rows)
