import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def sample_document(session, admin_user):
    from app.models.file import File
    from app.models.document import Document

    f = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://test-bucket/protocol.pdf",
        filename="protocol.pdf",
        size_bytes=50000,
        file_type="document",
        uploader_user_id=admin_user.id,
    )
    session.add(f)
    await session.flush()

    doc = Document(
        organization_id=admin_user.organization_id,
        file_id=f.id,
        title="Test Protocol",
    )
    session.add(doc)
    await session.flush()
    await session.commit()
    return doc


@pytest.mark.asyncio
async def test_list_documents(client, admin_token, sample_document):
    resp = await client.get(
        "/api/documents",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(d["title"] == "Test Protocol" for d in data["documents"])


@pytest.mark.asyncio
async def test_get_document(client, admin_token, sample_document):
    resp = await client.get(
        f"/api/documents/{sample_document.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Test Protocol"


@pytest.mark.asyncio
async def test_get_document_file_uri_sources_storage_uri(client, admin_token, sample_document, session):
    """Stage 3a: the embedded FileResponse.gcs_uri is sourced from the neutral
    storage_uri column. Diverge the columns (raw UPDATE bypasses the sync shim)
    and assert the response carries the storage_uri value."""
    from sqlalchemy import text

    neutral = "gs://neutral-bucket/protocol-from-storage-uri.pdf"
    await session.execute(
        text("UPDATE files SET storage_uri = :s WHERE gcs_uri = :g").bindparams(
            s=neutral, g="gs://test-bucket/protocol.pdf"
        )
    )
    await session.commit()

    resp = await client.get(
        f"/api/documents/{sample_document.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["file"]["gcs_uri"] == neutral
    assert resp.json()["file"]["storage_uri"] == neutral


@pytest.mark.asyncio
async def test_update_document(client, admin_token, sample_document):
    resp = await client.patch(
        f"/api/documents/{sample_document.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "Updated Protocol"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated Protocol"


@pytest.mark.asyncio
async def test_delete_document(client, admin_token, sample_document):
    resp = await client.delete(
        f"/api/documents/{sample_document.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_delete_document(client, viewer_token, sample_document):
    resp = await client.delete(
        f"/api/documents/{sample_document.id}",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403
