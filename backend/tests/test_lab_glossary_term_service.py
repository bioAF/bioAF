"""Lab Glossary term service (ADR-062), Phase B acceptance criteria.

Covers manual entry (AC-B01), case-insensitive duplicate detection (AC-B02),
edit-with-history (AC-B07 mechanism), delete (AC-B11 mechanism), list/filter,
and audit logging (AC-B12).
"""

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.lab_glossary import LabGlossaryTerm, LabGlossaryTermHistory
from app.services.lab_glossary_service import (
    DuplicateTermError,
    LabGlossaryService,
)


async def _audit_actions(session, entity_type, entity_id):
    rows = (
        (
            await session.execute(
                select(AuditLog.action).where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@pytest.mark.asyncio
async def test_create_manual_term_appears_in_list_and_audits(session, admin_user):
    # AC-B01, AC-B12
    org_id, uid = admin_user.organization_id, admin_user.id
    term = await LabGlossaryService.create_term(
        session,
        org_id=org_id,
        user_id=uid,
        term="Visually Acceptable Oocyte",
        definition="An oocyte that meets the lab's morphology bar at intake.",
        aliases=["VAO"],
        category="QC",
        context="Used at sample intake QC",
    )
    assert term.source == "manual"
    terms, total = await LabGlossaryService.list_terms(session, org_id=org_id)
    assert total == 1 and terms[0].term == "Visually Acceptable Oocyte"
    assert "created" in await _audit_actions(session, "lab_glossary_term", term.id)


@pytest.mark.asyncio
async def test_duplicate_term_is_case_insensitive(session, admin_user):
    # AC-B02
    org_id, uid = admin_user.organization_id, admin_user.id
    first = await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Passage 3", definition="3rd passage."
    )
    with pytest.raises(DuplicateTermError) as exc:
        await LabGlossaryService.create_term(session, org_id=org_id, user_id=uid, term="passage 3", definition="dup")
    assert exc.value.existing_term_id == first.id


@pytest.mark.asyncio
async def test_update_term_writes_history_and_audits(session, admin_user):
    # AC-B07 mechanism, AC-B12
    org_id, uid = admin_user.organization_id, admin_user.id
    term = await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Threshold", definition="old def", category="QC"
    )
    await LabGlossaryService.update_term(session, org_id=org_id, user_id=uid, term_id=term.id, definition="new def")
    refreshed = await LabGlossaryService.get_term(session, term_id=term.id, org_id=org_id)
    assert refreshed.definition == "new def"
    history = (
        (await session.execute(select(LabGlossaryTermHistory).where(LabGlossaryTermHistory.term_id == term.id)))
        .scalars()
        .all()
    )
    assert len(history) == 1 and history[0].previous_definition == "old def"
    assert "updated" in await _audit_actions(session, "lab_glossary_term", term.id)


@pytest.mark.asyncio
async def test_delete_term_records_content_in_audit(session, admin_user):
    # AC-B11 mechanism, AC-B12
    org_id, uid = admin_user.organization_id, admin_user.id
    term = await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Obsolete", definition="gone soon"
    )
    deleted = await LabGlossaryService.delete_term(session, org_id=org_id, user_id=uid, term_id=term.id)
    assert deleted is True
    assert (
        await session.execute(select(LabGlossaryTerm).where(LabGlossaryTerm.id == term.id))
    ).scalar_one_or_none() is None
    rows = (
        (
            await session.execute(
                select(AuditLog).where(AuditLog.entity_type == "lab_glossary_term", AuditLog.action == "deleted")
            )
        )
        .scalars()
        .all()
    )
    assert rows and rows[0].details_json.get("term") == "Obsolete"


@pytest.mark.asyncio
async def test_list_filters_by_category_and_source(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="A", definition="d", category="QC", source="manual"
    )
    await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="B", definition="d", category="Ops", source="llm_scan"
    )
    qc, qc_total = await LabGlossaryService.list_terms(session, org_id=org_id, category="QC")
    assert qc_total == 1 and qc[0].term == "A"
    scanned, scan_total = await LabGlossaryService.list_terms(session, org_id=org_id, source="llm_scan")
    assert scan_total == 1 and scanned[0].term == "B"


@pytest.mark.asyncio
async def test_list_search_matches_term_and_definition(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Centrifuge", definition="spins samples"
    )
    await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Incubator", definition="warms samples"
    )
    hits, total = await LabGlossaryService.list_terms(session, org_id=org_id, query="spins")
    assert total == 1 and hits[0].term == "Centrifuge"


@pytest.mark.asyncio
async def test_org_isolation_on_get(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    term = await LabGlossaryService.create_term(session, org_id=org_id, user_id=uid, term="Mine", definition="d")
    assert await LabGlossaryService.get_term(session, term_id=term.id, org_id=org_id + 999) is None
