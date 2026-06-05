"""Lab Glossary scan + proposal-review service (ADR-062), Phase B.

Covers the in-process scan executor (mirroring the Agent Review job pattern),
the dedup / previously-rejected logic, CSV import parsing, and the proposal
review/commit flow. LLM and source-content I/O are injected so these tests
exercise pure DB behavior. Covers AC-B03..B10, AC-B12.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog
from app.models.lab_glossary import (
    LabGlossaryRejectedProposal,
    LabGlossaryScanJob,
    LabGlossaryScanProposal,
    LabGlossaryTerm,
)
from app.services import lab_glossary_scan_service as scan_svc
from app.services.lab_glossary_service import LabGlossaryService


def _factory(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


async def _proposals(session, job_id):
    return (
        await session.execute(
            select(LabGlossaryScanProposal).where(LabGlossaryScanProposal.scan_job_id == job_id)
        )
    ).scalars().all()


async def _audit_actions(session, entity_type):
    return (
        await session.execute(select(AuditLog.action).where(AuditLog.entity_type == entity_type))
    ).scalars().all()


# --- scan job creation + execution ------------------------------------------


@pytest.mark.asyncio
async def test_create_scan_job_is_pending_and_audited(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    job = await scan_svc.create_scan_job(
        session, org_id=org_id, user_id=uid, scan_type="topic", scan_input="10x scRNA-seq"
    )
    assert job.status == "pending" and job.scan_type == "topic"
    await session.commit()
    assert "initiated" in await _audit_actions(session, "lab_glossary_scan_job")


@pytest.mark.asyncio
async def test_execute_scan_writes_new_and_changed_and_skips_unchanged(session, admin_user, db_engine):
    # AC-B05, AC-B06
    org_id, uid = admin_user.organization_id, admin_user.id
    # Existing term whose definition will be unchanged (skip) and another that changes.
    await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Oocyte", definition="An immature egg cell."
    )
    await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Passage", definition="old definition"
    )
    job = await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="platform_wide")
    await session.commit()

    async def fake_submit(*, prompt, payload, model, api_key):
        return (
            '[{"term": "Oocyte", "definition": "An immature egg cell."},'
            ' {"term": "Passage", "definition": "a NEW meaning"},'
            ' {"term": "Cryoprotectant", "definition": "Protects cells during freezing."}]'
        )

    async def fake_content(session, job):
        return "some platform content"

    await scan_svc.execute_scan(
        _factory(db_engine), job_id=job.id, content_provider=fake_content, submit_override=fake_submit
    )

    props = await _proposals(session, job.id)
    by_term = {p.term: p for p in props}
    assert "Oocyte" not in by_term  # unchanged -> skipped
    assert by_term["Passage"].proposal_type == "changed"
    assert by_term["Passage"].existing_term_id is not None
    assert by_term["Cryoprotectant"].proposal_type == "new"

    async with _factory(db_engine)() as s2:  # fresh session: avoid stale identity-map copy
        refreshed = (
            await s2.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.id == job.id))
        ).scalar_one()
        assert refreshed.status == "complete"
        assert refreshed.proposed_new_count == 1 and refreshed.proposed_changed_count == 1


@pytest.mark.asyncio
async def test_execute_scan_flags_previously_rejected(session, admin_user, db_engine):
    # AC-B09
    org_id, uid = admin_user.organization_id, admin_user.id
    session.add(
        LabGlossaryRejectedProposal(
            organization_id=org_id,
            term="Spheroid",
            proposed_definition="some prior def",
            proposed_source="platform_wide",
            rejected_by_user_id=uid,
        )
    )
    job = await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="platform_wide")
    await session.commit()

    async def fake_submit(*, prompt, payload, model, api_key):
        return '[{"term": "Spheroid", "definition": "A 3D cell cluster."}]'

    await scan_svc.execute_scan(
        _factory(db_engine), job_id=job.id, content_provider=lambda s, j: _async("x"), submit_override=fake_submit
    )
    props = await _proposals(session, job.id)
    assert len(props) == 1 and props[0].previously_rejected is True


@pytest.mark.asyncio
async def test_execute_scan_failure_marks_job_failed(session, admin_user, db_engine):
    org_id, uid = admin_user.organization_id, admin_user.id
    job = await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="topic", scan_input="x")
    await session.commit()

    async def boom(*, prompt, payload, model, api_key):
        raise RuntimeError("provider exploded")

    await scan_svc.execute_scan(
        _factory(db_engine), job_id=job.id, content_provider=lambda s, j: _async("x"), submit_override=boom
    )
    async with _factory(db_engine)() as s2:
        refreshed = (
            await s2.execute(select(LabGlossaryScanJob).where(LabGlossaryScanJob.id == job.id))
        ).scalar_one()
        assert refreshed.status == "failed" and refreshed.error_message


# --- CSV import --------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_import_produces_proposals(session, admin_user):
    # AC-B03
    org_id, uid = admin_user.organization_id, admin_user.id
    csv = "term,definition,aliases,category\nVAO,Visually acceptable oocyte,VAO|VAOocyte,QC\nP3,Passage three,,Ops\n"
    job = await scan_svc.parse_csv_import(session, org_id=org_id, user_id=uid, content=csv)
    assert job.scan_type == "import"
    props = await _proposals(session, job.id)
    assert {p.term for p in props} == {"VAO", "P3"}
    vao = next(p for p in props if p.term == "VAO")
    assert vao.proposal_type == "new" and vao.proposed_aliases == ["VAO", "VAOocyte"]


@pytest.mark.asyncio
async def test_csv_import_missing_required_column_raises(session, admin_user):
    # AC-B04
    org_id, uid = admin_user.organization_id, admin_user.id
    with pytest.raises(scan_svc.CsvParseError):
        await scan_svc.parse_csv_import(
            session, org_id=org_id, user_id=uid, content="term,notes\nFoo,bar\n"
        )


@pytest.mark.asyncio
async def test_csv_import_rejects_over_500_rows(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    rows = "\n".join(f"T{i},def{i}" for i in range(501))
    with pytest.raises(scan_svc.CsvParseError):
        await scan_svc.parse_csv_import(
            session, org_id=org_id, user_id=uid, content="term,definition\n" + rows + "\n"
        )


# --- review / commit ---------------------------------------------------------


@pytest.mark.asyncio
async def test_review_accept_new_creates_term(session, admin_user):
    # AC-B12 (created via review), source = import for import jobs
    org_id, uid = admin_user.organization_id, admin_user.id
    job = await scan_svc.parse_csv_import(
        session, org_id=org_id, user_id=uid, content="term,definition\nGastruloid,A 3D embryo model\n"
    )
    prop = (await _proposals(session, job.id))[0]
    summary = await scan_svc.review_proposals(
        session, org_id=org_id, user_id=uid, job_id=job.id, decisions={prop.id: "accepted"}
    )
    assert summary["accepted"] == 1
    term = await LabGlossaryService.get_by_term(session, org_id=org_id, term="Gastruloid")
    assert term is not None and term.source == "import"


@pytest.mark.asyncio
async def test_review_accept_changed_updates_term_and_history(session, admin_user, db_engine):
    # AC-B07
    org_id, uid = admin_user.organization_id, admin_user.id
    existing = await LabGlossaryService.create_term(
        session, org_id=org_id, user_id=uid, term="Confluence", definition="old"
    )
    job = await scan_svc.create_scan_job(session, org_id=org_id, user_id=uid, scan_type="topic", scan_input="x")
    await session.commit()

    async def fake_submit(*, prompt, payload, model, api_key):
        return '[{"term": "Confluence", "definition": "fraction of surface covered by cells"}]'

    await scan_svc.execute_scan(
        _factory(db_engine), job_id=job.id, content_provider=lambda s, j: _async("x"), submit_override=fake_submit
    )
    prop = (await _proposals(session, job.id))[0]
    assert prop.proposal_type == "changed"
    await scan_svc.review_proposals(
        session, org_id=org_id, user_id=uid, job_id=job.id, decisions={prop.id: "accepted"}
    )
    refreshed = await LabGlossaryService.get_term(session, term_id=existing.id, org_id=org_id)
    assert refreshed.definition == "fraction of surface covered by cells"
    from app.models.lab_glossary import LabGlossaryTermHistory

    hist = (
        await session.execute(
            select(LabGlossaryTermHistory).where(LabGlossaryTermHistory.term_id == existing.id)
        )
    ).scalars().all()
    assert hist and hist[0].previous_definition == "old"


@pytest.mark.asyncio
async def test_review_reject_writes_rejected_proposal(session, admin_user):
    # AC-B08
    org_id, uid = admin_user.organization_id, admin_user.id
    job = await scan_svc.parse_csv_import(
        session, org_id=org_id, user_id=uid, content="term,definition\nJunk,unwanted\n"
    )
    prop = (await _proposals(session, job.id))[0]
    await scan_svc.review_proposals(
        session, org_id=org_id, user_id=uid, job_id=job.id, decisions={prop.id: "rejected"}
    )
    rejected = (
        await session.execute(
            select(LabGlossaryRejectedProposal).where(LabGlossaryRejectedProposal.term == "Junk")
        )
    ).scalars().all()
    assert len(rejected) == 1 and rejected[0].proposed_source == "import"
    assert await LabGlossaryService.get_by_term(session, org_id=org_id, term="Junk") is None


@pytest.mark.asyncio
async def test_review_accept_all_remaining_commits_pending(session, admin_user):
    # AC-B10
    org_id, uid = admin_user.organization_id, admin_user.id
    job = await scan_svc.parse_csv_import(
        session, org_id=org_id, user_id=uid, content="term,definition\nA,da\nB,db\nC,dc\n"
    )
    summary = await scan_svc.review_proposals(
        session, org_id=org_id, user_id=uid, job_id=job.id, decisions={}, accept_all_remaining=True
    )
    assert summary["accepted"] == 3
    terms, total = await LabGlossaryService.list_terms(session, org_id=org_id)
    assert total == 3


@pytest.mark.asyncio
async def test_review_writes_audit_summary(session, admin_user):
    # AC-B12
    org_id, uid = admin_user.organization_id, admin_user.id
    job = await scan_svc.parse_csv_import(
        session, org_id=org_id, user_id=uid, content="term,definition\nA,da\n"
    )
    prop = (await _proposals(session, job.id))[0]
    await scan_svc.review_proposals(
        session, org_id=org_id, user_id=uid, job_id=job.id, decisions={prop.id: "accepted"}
    )
    assert "proposals_reviewed" in await _audit_actions(session, "lab_glossary_proposals_reviewed")


# small helper to let a lambda return an awaitable
async def _async(v):
    return v
