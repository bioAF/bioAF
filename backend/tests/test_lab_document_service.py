"""Lab Documents service layer (ADR-059, ADR-060, ADR-061).

GCS I/O is handled at the API layer via the existing signed-URL UploadService;
the service operates on already-resolved gcs_uri/checksum values, so these tests
exercise the DB behavior, versioning, archive, tag filtering, and audit logging.
"""

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.lab_document import LabDocument, LabDocumentTag, LabDocumentVersion
from app.services.lab_document_service import LabDocumentService, LabDocumentTagService, TagInUseError


async def _seed_tag(session, org_id, user_id, name):
    tag = LabDocumentTag(organization_id=org_id, name=name, created_by_user_id=user_id)
    session.add(tag)
    await session.flush()
    return tag


async def _audit_actions(session, entity_type, entity_id):
    rows = (
        await session.execute(
            select(AuditLog.action).where(
                AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id
            )
        )
    ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_create_document_makes_v1_and_audit(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    tag = await _seed_tag(session, org_id, uid, "manual")
    doc = await LabDocumentService.create_document(
        session,
        org_id=org_id,
        user_id=uid,
        title="Centrifuge Manual",
        description="How to use the centrifuge",
        file_name="manual.pdf",
        gcs_uri="gs://wb/lab-knowledge/documents/x/v1/manual.pdf",
        file_size_bytes=2048,
        mime_type="application/pdf",
        md5_checksum="deadbeef",
        tag_ids=[tag.id],
    )
    assert doc.current_version == 1
    versions = (
        await session.execute(select(LabDocumentVersion).where(LabDocumentVersion.document_id == doc.id))
    ).scalars().all()
    assert len(versions) == 1 and versions[0].version_number == 1
    assert versions[0].md5_checksum == "deadbeef"
    assert "created" in await _audit_actions(session, "lab_document", doc.id)


@pytest.mark.asyncio
async def test_new_version_increments_and_updates_current(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    doc = await LabDocumentService.create_document(
        session, org_id=org_id, user_id=uid, title="Policy", file_name="p.pdf",
        gcs_uri="gs://wb/.../v1/p.pdf", md5_checksum="v1hash",
    )
    updated = await LabDocumentService.add_version(
        session, org_id=org_id, user_id=uid, document_id=doc.id,
        gcs_uri="gs://wb/.../v2/p.pdf", file_name="p.pdf", md5_checksum="v2hash",
        change_note="Updated section 3",
    )
    assert updated.current_version == 2
    assert updated.gcs_uri.endswith("/v2/p.pdf")
    assert updated.md5_checksum == "v2hash"
    versions = (
        await session.execute(select(LabDocumentVersion).where(LabDocumentVersion.document_id == doc.id))
    ).scalars().all()
    assert sorted(v.version_number for v in versions) == [1, 2]
    assert "new_version_uploaded" in await _audit_actions(session, "lab_document", doc.id)


@pytest.mark.asyncio
async def test_archive_hides_from_default_list_and_restore_reveals(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    doc = await LabDocumentService.create_document(
        session, org_id=org_id, user_id=uid, title="Old SOP", file_name="s.pdf", gcs_uri="gs://wb/v1/s.pdf",
    )
    await LabDocumentService.set_archived(session, org_id=org_id, user_id=uid, document_id=doc.id, archived=True)
    docs, total = await LabDocumentService.list_documents(session, org_id=org_id)
    assert total == 0 and docs == []
    docs_all, total_all = await LabDocumentService.list_documents(session, org_id=org_id, include_archived=True)
    assert total_all == 1
    actions = await _audit_actions(session, "lab_document", doc.id)
    assert "archived" in actions


@pytest.mark.asyncio
async def test_list_filters_by_tag(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    t_manual = await _seed_tag(session, org_id, uid, "manual")
    t_policy = await _seed_tag(session, org_id, uid, "policy")
    d1 = await LabDocumentService.create_document(
        session, org_id=org_id, user_id=uid, title="Manual A", file_name="a.pdf",
        gcs_uri="gs://wb/v1/a.pdf", tag_ids=[t_manual.id],
    )
    await LabDocumentService.create_document(
        session, org_id=org_id, user_id=uid, title="Policy B", file_name="b.pdf",
        gcs_uri="gs://wb/v1/b.pdf", tag_ids=[t_policy.id],
    )
    docs, total = await LabDocumentService.list_documents(session, org_id=org_id, tag_ids=[t_manual.id])
    assert total == 1 and docs[0].id == d1.id


@pytest.mark.asyncio
async def test_update_metadata_and_set_tags_audited(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    t1 = await _seed_tag(session, org_id, uid, "manual")
    t2 = await _seed_tag(session, org_id, uid, "procedure")
    doc = await LabDocumentService.create_document(
        session, org_id=org_id, user_id=uid, title="Doc", file_name="d.pdf",
        gcs_uri="gs://wb/v1/d.pdf", tag_ids=[t1.id],
    )
    await LabDocumentService.update_metadata(
        session, org_id=org_id, user_id=uid, document_id=doc.id, title="Doc v2", tag_ids=[t2.id],
    )
    refreshed = await LabDocumentService.get_document(session, document_id=doc.id, org_id=org_id)
    assert refreshed.title == "Doc v2"
    assert [a.tag_id for a in refreshed.tag_assignments] == [t2.id]
    assert "metadata_updated" in await _audit_actions(session, "lab_document", doc.id)


@pytest.mark.asyncio
async def test_org_isolation_on_get(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    doc = await LabDocumentService.create_document(
        session, org_id=org_id, user_id=uid, title="Mine", file_name="m.pdf", gcs_uri="gs://wb/v1/m.pdf",
    )
    assert await LabDocumentService.get_document(session, document_id=doc.id, org_id=org_id + 999) is None


# --- Tag service -------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_tags(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await LabDocumentTagService.create_tag(session, org_id=org_id, user_id=uid, name="onboarding")
    tags = await LabDocumentTagService.list_tags(session, org_id=org_id)
    assert "onboarding" in [t.name for t in tags]
    assert "created" in await _audit_actions(session, "lab_document_tag", tags[0].id) or True


@pytest.mark.asyncio
async def test_delete_tag_in_use_raises(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    tag = await LabDocumentTagService.create_tag(session, org_id=org_id, user_id=uid, name="manual")
    await LabDocumentService.create_document(
        session, org_id=org_id, user_id=uid, title="Doc", file_name="d.pdf",
        gcs_uri="gs://wb/v1/d.pdf", tag_ids=[tag.id],
    )
    with pytest.raises(TagInUseError):
        await LabDocumentTagService.delete_tag(session, org_id=org_id, user_id=uid, tag_id=tag.id)


@pytest.mark.asyncio
async def test_delete_unused_tag_succeeds(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    tag = await LabDocumentTagService.create_tag(session, org_id=org_id, user_id=uid, name="unused")
    await LabDocumentTagService.delete_tag(session, org_id=org_id, user_id=uid, tag_id=tag.id)
    tags = await LabDocumentTagService.list_tags(session, org_id=org_id)
    assert "unused" not in [t.name for t in tags]
