"""Lab documents surface in global search (AC-A09, F-LKA-07)."""

import pytest

from app.services.lab_document_service import LabDocumentService
from app.services.search_service import FULL_SEARCH_TYPES, SearchService


@pytest.mark.asyncio
async def test_lab_document_is_a_full_search_type():
    assert "lab_document" in FULL_SEARCH_TYPES


@pytest.mark.asyncio
async def test_full_search_finds_lab_document_by_title(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await LabDocumentService.create_document(
        session,
        org_id=org_id,
        user_id=uid,
        title="Centrifuge Operating Manual",
        description="how to spin",
        file_name="c.pdf",
        storage_uri="gs://wb/v1/c.pdf",
    )
    results, total, counts = await SearchService.full_search(
        session, org_id, "Centrifuge", entity_types=["lab_document"], count_types=["lab_document"]
    )
    assert total == 1
    assert results[0]["entity_type"] == "lab_document"
    assert results[0]["url"].startswith("/lab-knowledge/documents")
    assert counts["lab_document"] == 1


@pytest.mark.asyncio
async def test_full_search_excludes_archived_lab_documents(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    doc = await LabDocumentService.create_document(
        session,
        org_id=org_id,
        user_id=uid,
        title="Archived Cleaning Schedule",
        file_name="a.pdf",
        storage_uri="gs://wb/v1/a.pdf",
    )
    await LabDocumentService.set_archived(session, org_id=org_id, user_id=uid, document_id=doc.id, archived=True)
    results, total, _ = await SearchService.full_search(
        session, org_id, "Cleaning", entity_types=["lab_document"], count_types=["lab_document"]
    )
    assert total == 0


@pytest.mark.asyncio
async def test_quick_search_finds_lab_document(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    await LabDocumentService.create_document(
        session,
        org_id=org_id,
        user_id=uid,
        title="Onboarding Checklist",
        file_name="o.pdf",
        storage_uri="gs://wb/v1/o.pdf",
    )
    hits = await SearchService.quick_search(session, org_id, "Onboarding")
    assert any(h["entity_type"] == "lab_document" and h["name"] == "Onboarding Checklist" for h in hits)
