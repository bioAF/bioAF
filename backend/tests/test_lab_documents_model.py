"""Lab Documents data model (ADR-061): documents, versions, tags, assignments."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.lab_document import (
    LabDocument,
    LabDocumentTag,
    LabDocumentTagAssignment,
    LabDocumentVersion,
)


async def _make_doc(session, org_id, user_id, **overrides):
    doc = LabDocument(
        organization_id=org_id,
        title=overrides.get("title", "Centrifuge Manual"),
        description=overrides.get("description"),
        gcs_uri=overrides.get("gcs_uri", "gs://bucket/lab-knowledge/documents/1/v1/manual.pdf"),
        file_name=overrides.get("file_name", "manual.pdf"),
        file_size_bytes=overrides.get("file_size_bytes", 1234),
        mime_type=overrides.get("mime_type", "application/pdf"),
        md5_checksum=overrides.get("md5_checksum", "abc123"),
        created_by_user_id=user_id,
    )
    session.add(doc)
    await session.flush()
    return doc


@pytest.mark.asyncio
async def test_create_document_defaults(session, admin_user):
    doc = await _make_doc(session, admin_user.organization_id, admin_user.id)
    assert doc.id is not None
    assert doc.current_version == 1
    assert doc.is_archived is False


@pytest.mark.asyncio
async def test_document_version_unique_per_number(session, admin_user):
    doc = await _make_doc(session, admin_user.organization_id, admin_user.id)
    session.add(
        LabDocumentVersion(
            document_id=doc.id,
            version_number=1,
            gcs_uri="gs://bucket/.../v1/manual.pdf",
            file_name="manual.pdf",
            uploaded_by_user_id=admin_user.id,
        )
    )
    await session.flush()
    session.add(
        LabDocumentVersion(
            document_id=doc.id,
            version_number=1,
            gcs_uri="gs://bucket/.../v1/dupe.pdf",
            file_name="dupe.pdf",
            uploaded_by_user_id=admin_user.id,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_tag_unique_per_org(session, admin_user):
    org_id = admin_user.organization_id
    session.add(LabDocumentTag(organization_id=org_id, name="procedure", created_by_user_id=admin_user.id))
    await session.flush()
    session.add(LabDocumentTag(organization_id=org_id, name="procedure", created_by_user_id=admin_user.id))
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_tag_assignment_links_document_and_tag(session, admin_user):
    org_id = admin_user.organization_id
    doc = await _make_doc(session, org_id, admin_user.id)
    tag = LabDocumentTag(organization_id=org_id, name="manual", created_by_user_id=admin_user.id)
    session.add(tag)
    await session.flush()
    session.add(LabDocumentTagAssignment(document_id=doc.id, tag_id=tag.id))
    await session.flush()

    rows = (
        (await session.execute(select(LabDocumentTagAssignment).where(LabDocumentTagAssignment.document_id == doc.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].tag_id == tag.id
