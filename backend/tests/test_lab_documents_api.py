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
    with (
        patch(
            f"{UPLOAD}.read_metadata",
            new_callable=AsyncMock,
            return_value={"file_name": file_name, "mime_type": mime, "size_bytes": size, "md5": md5},
        ),
        patch(
            f"{UPLOAD}.place",
            new_callable=AsyncMock,
            side_effect=lambda *a, **k: (
                f"gs://wb/lab-knowledge/documents/{k['document_id']}/v{k['version']}/{file_name}"
            ),
        ),
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
    tag = (
        await client.post("/api/lab-knowledge/document-tags", json={"name": "manual"}, headers=_auth(admin_token))
    ).json()
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

    def fake_session(bucket_name, blob_path, *, content_type, size_bytes, origin=None):
        captured["origin"] = origin
        captured["content_type"] = content_type
        captured["size_bytes"] = size_bytes
        return "https://storage.example/resumable/session"

    with (
        patch(f"{UPLOAD}._get_working_bucket", new_callable=AsyncMock, return_value="wb"),
        patch(f"{UPLOAD}._create_resumable_session", new_callable=AsyncMock, side_effect=fake_session),
    ):
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
async def test_import_from_url_enqueues_then_executor_creates_document(client, admin_token):
    # AC: a manager can add a document by having the server pull it from a URL,
    # matching the Reference Data "URL import" option. The fetch is decoupled from
    # the request: the POST enqueues a job, and a background task reads the URL back
    # from the DB and runs the SSRF-guarded fetch. A numeric public IP avoids DNS so
    # the endpoint's up-front validation works offline.
    import app.database as database_module
    from app.services.lab_document_upload_service import LabDocumentUploadService

    # Stub the executor so the auto-dispatched background task is a no-op; we run
    # the real executor explicitly below with the network + GCS patched.
    with patch(f"{UPLOAD}.run_url_import", new_callable=AsyncMock):
        resp = await client.post(
            "/api/lab-knowledge/documents/import-url",
            json={"url": "http://8.8.8.8/policy.pdf", "tag_ids": []},
            headers=_auth(admin_token),
        )
    assert resp.status_code == 202, resp.text
    import_id = resp.json()["id"]
    assert resp.json()["status"] == "pending"

    async def fake_fetch(url):
        return (b"%PDF-1.4 body", "policy.pdf", "application/pdf")

    with (
        patch(f"{UPLOAD}._get_working_bucket", new_callable=AsyncMock, return_value="wb"),
        patch(
            "app.services.upload_service.UploadService._get_gcs_credentials", new_callable=AsyncMock, return_value=None
        ),
        patch(f"{UPLOAD}._upload_bytes", new_callable=AsyncMock, return_value=None),
        _patched_upload(file_name="policy.pdf", md5="urlmd5", size=12, mime="application/pdf"),
    ):
        await LabDocumentUploadService.run_url_import(
            database_module.async_session_factory, import_id=import_id, fetch_override=fake_fetch
        )

    status = await client.get(f"/api/lab-knowledge/documents/url-imports/{import_id}", headers=_auth(admin_token))
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "complete"
    assert status.json()["document_id"] is not None

    listed = await client.get("/api/lab-knowledge/documents", headers=_auth(admin_token))
    assert listed.json()["total"] == 1
    assert listed.json()["documents"][0]["file_name"] == "policy.pdf"


@pytest.mark.asyncio
async def test_url_import_executor_marks_failed_on_fetch_error(client, admin_token):
    import app.database as database_module
    from app.services.lab_document_upload_service import LabDocumentUploadService

    with patch(f"{UPLOAD}.run_url_import", new_callable=AsyncMock):
        resp = await client.post(
            "/api/lab-knowledge/documents/import-url",
            json={"url": "http://8.8.8.8/missing.pdf"},
            headers=_auth(admin_token),
        )
    import_id = resp.json()["id"]

    async def boom(url):
        raise ValueError("Could not fetch URL (HTTP 404)")

    await LabDocumentUploadService.run_url_import(
        database_module.async_session_factory, import_id=import_id, fetch_override=boom
    )
    status = await client.get(f"/api/lab-knowledge/documents/url-imports/{import_id}", headers=_auth(admin_token))
    assert status.json()["status"] == "failed"
    assert status.json()["document_id"] is None


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
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/computeMetadata/v1/",  # cloud metadata endpoint
        "http://127.0.0.1/secret",  # loopback
        "http://10.0.0.5/internal",  # private range
        "http://[::1]/secret",  # IPv6 loopback
    ],
)
async def test_import_from_url_blocks_ssrf_to_internal_hosts(client, admin_token, url):
    # SSRF guard: the server must refuse to fetch URLs that resolve to non-public
    # addresses, so a user cannot reach the instance metadata service or other
    # internal hosts. No network is needed (numeric IPs resolve locally).
    resp = await client.post(
        "/api/lab-knowledge/documents/import-url",
        json={"url": url},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 400, resp.text


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
    t_manual = (
        await client.post("/api/lab-knowledge/document-tags", json={"name": "manual"}, headers=_auth(admin_token))
    ).json()
    t_policy = (
        await client.post("/api/lab-knowledge/document-tags", json={"name": "policy"}, headers=_auth(admin_token))
    ).json()
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
    tag = (
        await client.post("/api/lab-knowledge/document-tags", json={"name": "manual"}, headers=_auth(admin_token))
    ).json()
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
        (
            await session.execute(
                select(AuditLog.action).where(
                    AuditLog.entity_type == "lab_document", AuditLog.entity_id == created["id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert "created" in actions and "archived" in actions


@pytest.mark.asyncio
async def test_content_streams_document_bytes(client, admin_token):
    # The inline viewer reads bytes through the backend (no GCS CORS), like the
    # literature paper PDF viewer.
    created = (await _create_doc(client, admin_token, file_name="manual.pdf")).json()
    with patch(
        "app.api.lab_documents._download_document_bytes",
        new_callable=AsyncMock,
        return_value=b"%PDF-1.4 inline bytes",
    ):
        resp = await client.get(f"/api/lab-knowledge/documents/{created['id']}/content", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-1.4 inline bytes"
    assert resp.headers["content-type"].startswith("application/pdf")


@pytest.mark.asyncio
async def test_content_missing_document_404(client, admin_token):
    resp = await client.get("/api/lab-knowledge/documents/99999/content", headers=_auth(admin_token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_list_and_delete_notes(client, admin_token):
    created = (await _create_doc(client, admin_token)).json()
    doc_id = created["id"]

    added = await client.post(
        f"/api/lab-knowledge/documents/{doc_id}/notes",
        json={"body": "Check section 3 for the spin protocol."},
        headers=_auth(admin_token),
    )
    assert added.status_code == 200, added.text
    note_id = added.json()["id"]
    assert added.json()["body"] == "Check section 3 for the spin protocol."

    listed = await client.get(f"/api/lab-knowledge/documents/{doc_id}/notes", headers=_auth(admin_token))
    assert listed.status_code == 200
    assert [n["id"] for n in listed.json()] == [note_id]

    deleted = await client.delete(f"/api/lab-knowledge/documents/{doc_id}/notes/{note_id}", headers=_auth(admin_token))
    assert deleted.status_code == 200

    after = await client.get(f"/api/lab-knowledge/documents/{doc_id}/notes", headers=_auth(admin_token))
    # Soft-deleted notes remain listed but are flagged so the UI can show [deleted].
    assert after.json()[0]["deleted"] is True


@pytest.mark.asyncio
async def test_viewer_can_add_note_but_not_delete_others(client, admin_token, viewer_token):
    created = (await _create_doc(client, admin_token)).json()
    doc_id = created["id"]

    # An admin's note.
    admin_note = (
        await client.post(
            f"/api/lab-knowledge/documents/{doc_id}/notes",
            json={"body": "Admin note"},
            headers=_auth(admin_token),
        )
    ).json()

    # A viewer can read documents and add their own note.
    viewer_add = await client.post(
        f"/api/lab-knowledge/documents/{doc_id}/notes",
        json={"body": "Viewer note"},
        headers=_auth(viewer_token),
    )
    assert viewer_add.status_code == 200

    # But cannot delete someone else's note (not the owner, lacks manage).
    blocked = await client.delete(
        f"/api/lab-knowledge/documents/{doc_id}/notes/{admin_note['id']}", headers=_auth(viewer_token)
    )
    assert blocked.status_code == 403


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

    with (
        patch("app.services.gcs_storage.GcsStorageService.get_credentials", new_callable=AsyncMock, return_value=None),
        patch("google.cloud.storage.Client", return_value=fake_client),
    ):
        resp = await client.get(f"/api/lab-knowledge/documents/{created['id']}/download", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json()["download_url"] == "https://signed.example/get"

    actions = (
        (
            await session.execute(
                select(AuditLog.action).where(
                    AuditLog.entity_type == "lab_document", AuditLog.entity_id == created["id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert "downloaded" in actions
