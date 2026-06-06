"""Unified document/file search API (LK-SPEC-D, F-LKD-04).

``GET /api/files/search`` returns the normalized union of files and lab documents,
gated per store on the caller's view permission. Covers AC-D07, AC-D08, AC-D10
(href shape consumed by the Data & Files page).
"""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seeded(session, admin_user):
    from app.models.file import File
    from app.models.lab_document import LabDocument

    session.add(
        File(
            organization_id=admin_user.organization_id,
            gcs_uri="gs://b/assay_protocol.pdf",
            filename="assay_protocol.pdf",
            file_type="pdf",
            source_type="upload",
        )
    )
    session.add(
        LabDocument(
            organization_id=admin_user.organization_id,
            title="Assay SOP",
            description="standard procedure",
            gcs_uri="gs://d/assay_sop.pdf",
            current_version=1,
            file_name="assay_sop.pdf",
            created_by_user_id=admin_user.id,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_data_search_returns_both_stores(client, admin_token, seeded):
    # AC-D07, AC-D10
    resp = await client.get("/api/files/search?q=assay", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    by_kind = {i["kind"] for i in items}
    assert by_kind == {"file", "lab_document"}
    doc = next(i for i in items if i["kind"] == "lab_document")
    assert doc["href"] == f"/lab-knowledge/documents/{doc['id']}"


@pytest.mark.asyncio
async def test_data_search_files_only_without_lab_documents_scope(client, viewer_api_key, seeded):
    # AC-D08: the viewer key carries files:view but not lab_documents:view.
    resp = await client.get("/api/files/search?q=assay", headers=viewer_api_key["headers"])
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items and all(i["kind"] == "file" for i in items)


@pytest.mark.asyncio
async def test_data_search_empty_query_returns_nothing(client, admin_token, seeded):
    resp = await client.get("/api/files/search?q=", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []
