"""Lab Documents API (ADR-059, ADR-060, ADR-061) and Phase A acceptance criteria.

The signed-URL GCS mechanics in LabDocumentUploadService are patched so the tests
exercise endpoint behavior, RBAC, versioning, archive, tags, and audit without a
live GCS, mirroring how GcsStorageService is patched elsewhere.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

UPLOAD = "app.services.lab_document_upload_service.LabDocumentUploadService"


@contextmanager
def _patched_upload(file_name="manual.pdf", md5="abc123", size=2048, mime="application/pdf"):
    with patch(
        f"{UPLOAD}.read_metadata",
        new_callable=AsyncMock,
        return_value={"file_name": file_name, "mime_type": mime, "size_bytes": size, "md5": md5},
    ), patch(
        f"{UPLOAD}.place",
        new_callable=AsyncMock,
        side_effect=lambda *a, **k: f"gs://wb/lab-knowledge/documents/{k['document_id']}/v{k['version']}/{file_name}",
    ):
        yield


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _create_doc(client, token, *, title="Centrifuge Manual", tag_ids=None, file_name="manual.pdf", md5="abc123"):
    with _patched_upload(file_name=file_name, md5=md5):
        resp = await client.post(
            "/api/lab-knowledge/documents",
            json={"upload_token": "tok-1", "title": title, "tag_ids": tag_ids or []},
            headers=_auth(token),
        )
    return resp


@pytest.mark.asyncio
async def test_upload_creates_document_v1_with_tags(client, admin_token):
    # AC-A01 / AC-A10
    tag = (await client.post("/api/lab-knowledge/document-tags", json={"name": "manual"}, headers=_auth(admin_token))).json()
    resp = await _create_doc(client, admin_token, tag_ids=[tag["id"]])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_version"] == 1
    assert body["md5_checksum"] == "abc123"
    assert [t["name"] for t in body["tags"]] == ["manual"]

    listed = await client.get("/api/lab-knowledge/documents", headers=_auth(admin_token))
    assert listed.json()["total"] == 1


@pytest.mark.asyncio
async def test_viewer_cannot_upload(client, admin_token, viewer_token):
    # AC-A02
    resp = await _create_doc(client, viewer_token)
    assert resp.status_code == 403
    url_resp = await client.post(
        "/api/lab-knowledge/documents/upload-url",
        json={"file_name": "x.pdf"},
        headers=_auth(viewer_token),
    )
    assert url_resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_list(client, admin_token, viewer_token):
    await _create_doc(client, admin_token)
    resp = await client.get("/api/lab-knowledge/documents", headers=_auth(viewer_token))
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_filter_by_tag(client, admin_token):
    # AC-A03
    t_manual = (await client.post("/api/lab-knowledge/document-tags", json={"name": "manual"}, headers=_auth(admin_token))).json()
    t_policy = (await client.post("/api/lab-knowledge/document-tags", json={"name": "policy"}, headers=_auth(admin_token))).json()
    await _create_doc(client, admin_token, title="Manual A", tag_ids=[t_manual["id"]], file_name="a.pdf")
    await _create_doc(client, admin_token, title="Policy B", tag_ids=[t_policy["id"]], file_name="b.pdf")

    resp = await client.get(f"/api/lab-knowledge/documents?tag_ids={t_manual['id']}", headers=_auth(admin_token))
    body = resp.json()
    assert body["total"] == 1 and body["documents"][0]["title"] == "Manual A"


@pytest.mark.asyncio
async def test_new_version_increments_and_lists(client, admin_token):
    # AC-A04
    created = (await _create_doc(client, admin_token)).json()
    with _patched_upload(file_name="manual.pdf", md5="v2hash"):
        resp = await client.post(
            f"/api/lab-knowledge/documents/{created['id']}/versions",
            json={"upload_token": "tok-2", "change_note": "Revised"},
            headers=_auth(admin_token),
        )
    assert resp.status_code == 200
    assert resp.json()["current_version"] == 2

    versions = await client.get(f"/api/lab-knowledge/documents/{created['id']}/versions", headers=_auth(admin_token))
    nums = [v["version_number"] for v in versions.json()]
    assert nums == [1, 2]
    assert versions.json()[1]["change_note"] == "Revised"


@pytest.mark.asyncio
async def test_archive_hides_then_toggle_reveals(client, admin_token):
    # AC-A05
    created = (await _create_doc(client, admin_token)).json()
    arch = await client.post(f"/api/lab-knowledge/documents/{created['id']}/archive", headers=_auth(admin_token))
    assert arch.status_code == 200 and arch.json()["is_archived"] is True

    default_list = await client.get("/api/lab-knowledge/documents", headers=_auth(admin_token))
    assert default_list.json()["total"] == 0
    with_archived = await client.get("/api/lab-knowledge/documents?include_archived=true", headers=_auth(admin_token))
    assert with_archived.json()["total"] == 1


@pytest.mark.asyncio
async def test_new_tag_appears_in_selector(client, admin_token):
    # AC-A07
    await client.post("/api/lab-knowledge/document-tags", json={"name": "onboarding"}, headers=_auth(admin_token))
    tags = await client.get("/api/lab-knowledge/document-tags", headers=_auth(admin_token))
    assert "onboarding" in [t["name"] for t in tags.json()]


@pytest.mark.asyncio
async def test_delete_tag_in_use_returns_409(client, admin_token):
    # AC-A08
    tag = (await client.post("/api/lab-knowledge/document-tags", json={"name": "manual"}, headers=_auth(admin_token))).json()
    await _create_doc(client, admin_token, tag_ids=[tag["id"]])
    resp = await client.delete(f"/api/lab-knowledge/document-tags/{tag['id']}", headers=_auth(admin_token))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_audit_log_records_document_ops(client, admin_token, session):
    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    created = (await _create_doc(client, admin_token)).json()
    await client.post(f"/api/lab-knowledge/documents/{created['id']}/archive", headers=_auth(admin_token))

    actions = (
        await session.execute(
            select(AuditLog.action).where(
                AuditLog.entity_type == "lab_document", AuditLog.entity_id == created["id"]
            )
        )
    ).scalars().all()
    assert "created" in actions and "archived" in actions


@pytest.mark.asyncio
async def test_get_missing_document_404(client, admin_token):
    resp = await client.get("/api/lab-knowledge/documents/99999", headers=_auth(admin_token))
    assert resp.status_code == 404
