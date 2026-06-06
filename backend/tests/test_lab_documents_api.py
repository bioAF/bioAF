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
async def test_upload_url_returns_resumable_session_scoped_to_request_origin(client, admin_token):
    # Regression for the "Failed to fetch" upload bug: the browser PUTs directly
    # to GCS, so the upload URL must be a resumable session created with the
    # request Origin (otherwise the cross-origin PUT preflight is rejected and
    # nothing reaches the backend). Mirrors the references upload flow.
    captured: dict = {}

    def fake_session(bucket_name, blob_path, *, content_type, size_bytes, origin=None, credentials=None):
        captured["origin"] = origin
        captured["content_type"] = content_type
        captured["size_bytes"] = size_bytes
        return "https://storage.example/resumable/session"

    with patch(f"{UPLOAD}._get_working_bucket", new_callable=AsyncMock, return_value="wb"), patch(
        "app.services.upload_service.UploadService._get_gcs_credentials", new_callable=AsyncMock, return_value=None
    ), patch(f"{UPLOAD}._create_resumable_session", side_effect=fake_session):
        resp = await client.post(
            "/api/lab-knowledge/documents/upload-url",
            json={"file_name": "manual.pdf", "mime_type": "application/pdf", "size_bytes": 2048},
            headers={**_auth(admin_token), "origin": "https://app.example"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["signed_url"] == "https://storage.example/resumable/session"
    assert captured["origin"] == "https://app.example"
    assert captured["content_type"] == "application/pdf"
    assert captured["size_bytes"] == 2048


@pytest.mark.asyncio
async def test_import_from_url_creates_document_v1(client, admin_token):
    # AC: a manager can add a document by having the server pull it from a URL,
    # matching the Reference Data "URL import" option.
    with patch(
        f"{UPLOAD}._fetch_url",
        new_callable=AsyncMock,
        return_value=(b"%PDF-1.4 body", "policy.pdf", "application/pdf"),
    ), patch(f"{UPLOAD}._get_working_bucket", new_callable=AsyncMock, return_value="wb"), patch(
        "app.services.upload_service.UploadService._get_gcs_credentials", new_callable=AsyncMock, return_value=None
    ), patch(f"{UPLOAD}._upload_bytes", new_callable=AsyncMock, return_value=None), _patched_upload(
        file_name="policy.pdf", md5="urlmd5", size=12, mime="application/pdf"
    ):
        resp = await client.post(
            "/api/lab-knowledge/documents/import-url",
            json={"url": "https://example.com/policy.pdf", "tag_ids": []},
            headers=_auth(admin_token),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["file_name"] == "policy.pdf"
    assert body["current_version"] == 1
    assert body["md5_checksum"] == "urlmd5"

    listed = await client.get("/api/lab-knowledge/documents", headers=_auth(admin_token))
    assert listed.json()["total"] == 1


@pytest.mark.asyncio
async def test_viewer_cannot_import_from_url(client, viewer_token):
    resp = await client.post(
        "/api/lab-knowledge/documents/import-url",
        json={"url": "https://example.com/policy.pdf"},
        headers=_auth(viewer_token),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_import_from_url_rejects_non_http_scheme(client, admin_token):
    resp = await client.post(
        "/api/lab-knowledge/documents/import-url",
        json={"url": "ftp://example.com/policy.pdf"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 400


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


@pytest.mark.asyncio
async def test_download_missing_document_404(client, admin_token):
    resp = await client.get("/api/lab-knowledge/documents/99999/download", headers=_auth(admin_token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_returns_signed_url_and_audits(client, admin_token, session):
    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    created = (await _create_doc(client, admin_token)).json()

    blob = type("Blob", (), {"generate_signed_url": lambda self, **kw: "https://signed.example/get"})()
    bucket = type("Bucket", (), {"blob": lambda self, p: blob})()
    fake_client = type("Client", (), {"bucket": lambda self, b: bucket})()

    with patch("app.services.gcs_storage.GcsStorageService.get_credentials", new_callable=AsyncMock, return_value=None), patch(
        "google.cloud.storage.Client", return_value=fake_client
    ):
        resp = await client.get(
            f"/api/lab-knowledge/documents/{created['id']}/download", headers=_auth(admin_token)
        )
    assert resp.status_code == 200
    assert resp.json()["download_url"] == "https://signed.example/get"

    actions = (
        await session.execute(
            select(AuditLog.action).where(
                AuditLog.entity_type == "lab_document", AuditLog.entity_id == created["id"]
            )
        )
    ).scalars().all()
    assert "downloaded" in actions
