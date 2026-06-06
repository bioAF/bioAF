"""Unified document/file search service (LK-SPEC-D, F-LKD-04, D2 + D3).

One backend capability returns a normalized union of Data & Files ``File`` rows
and Lab Knowledge ``LabDocument`` rows for a text query, org-scoped and
permission-gated. Consumed by both the Data & Files browser (D3) and the glossary
scan document picker (D2). Covers AC-D07..D09.
"""

import pytest

from app.models.file import File
from app.models.lab_document import LabDocument
from app.services.data_search_service import unified_document_file_search


async def _seed(session, org_id, uid):
    session.add(
        File(
            organization_id=org_id,
            gcs_uri="gs://b/assay_protocol.pdf",
            filename="assay_protocol.pdf",
            file_type="pdf",
            size_bytes=2048,
            source_type="upload",
        )
    )
    session.add(
        LabDocument(
            organization_id=org_id,
            title="Assay SOP",
            description="Standard assay operating procedure",
            gcs_uri="gs://d/assay_sop_v1.pdf",
            current_version=1,
            file_name="assay_sop.pdf",
            mime_type="application/pdf",
            file_size_bytes=4096,
            created_by_user_id=uid,
        )
    )
    # An archived lab document that must never surface.
    session.add(
        LabDocument(
            organization_id=org_id,
            title="Assay old SOP",
            gcs_uri="gs://d/assay_old.pdf",
            current_version=1,
            file_name="assay_old.pdf",
            created_by_user_id=uid,
            is_archived=True,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_union_returns_both_stores(session, admin_user):
    # AC-D07
    org_id, uid = admin_user.organization_id, admin_user.id
    await _seed(session, org_id, uid)
    items = await unified_document_file_search(
        session, org_id=org_id, query="assay", include_files=True, include_lab_documents=True
    )
    kinds = {(i["kind"], i["name"]) for i in items}
    assert ("file", "assay_protocol.pdf") in kinds
    assert ("lab_document", "Assay SOP") in kinds
    # Normalized shape present on every item.
    for i in items:
        assert {
            "kind",
            "id",
            "name",
            "file_type",
            "size_bytes",
            "updated_at",
            "href",
            "experiment_id",
            "source",
        } <= set(i)
    doc = next(i for i in items if i["kind"] == "lab_document")
    assert doc["href"] == f"/lab-knowledge/documents/{doc['id']}"
    assert doc["source"] == "lab_knowledge"


@pytest.mark.asyncio
async def test_union_excludes_archived_lab_documents(session, admin_user):
    # AC-D09
    org_id, uid = admin_user.organization_id, admin_user.id
    await _seed(session, org_id, uid)
    items = await unified_document_file_search(
        session, org_id=org_id, query="assay", include_files=True, include_lab_documents=True
    )
    assert not any(i["name"] == "Assay old SOP" for i in items)


@pytest.mark.asyncio
async def test_union_respects_permission_gates(session, admin_user):
    # AC-D08
    org_id, uid = admin_user.organization_id, admin_user.id
    await _seed(session, org_id, uid)

    files_only = await unified_document_file_search(
        session, org_id=org_id, query="assay", include_files=True, include_lab_documents=False
    )
    assert files_only and all(i["kind"] == "file" for i in files_only)

    docs_only = await unified_document_file_search(
        session, org_id=org_id, query="assay", include_files=False, include_lab_documents=True
    )
    assert docs_only and all(i["kind"] == "lab_document" for i in docs_only)

    neither = await unified_document_file_search(
        session, org_id=org_id, query="assay", include_files=False, include_lab_documents=False
    )
    assert neither == []


@pytest.mark.asyncio
async def test_union_is_org_scoped(session, admin_user):
    org_id, uid = admin_user.organization_id, admin_user.id
    from app.models.organization import Organization

    await _seed(session, org_id, uid)
    other = Organization(name="Other", setup_complete=True)
    session.add(other)
    await session.flush()
    session.add(
        LabDocument(
            organization_id=other.id,
            title="Assay foreign",
            gcs_uri="gs://d/foreign.pdf",
            current_version=1,
            file_name="foreign.pdf",
            created_by_user_id=uid,
        )
    )
    await session.commit()
    items = await unified_document_file_search(
        session, org_id=org_id, query="assay", include_files=True, include_lab_documents=True
    )
    assert not any(i["name"] == "Assay foreign" for i in items)
