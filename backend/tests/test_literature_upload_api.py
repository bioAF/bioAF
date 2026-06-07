"""Upload endpoint: ASGI integration that exercises the synchronous extraction
path and verifies the resulting Paper has the right metadata."""

from __future__ import annotations


import pytest


def _build_test_pdf() -> bytes:
    """Make a small in-memory PDF with embedded metadata. PyMuPDF (fitz) is
    already a project dependency."""
    pytest.importorskip("fitz")
    import fitz  # type: ignore

    doc = fitz.open()
    page = doc.new_page()
    abstract = (
        "Abstract: Pancreatic ductal adenocarcinoma exhibits heterogeneity in "
        "TGF-beta signalling that suggests new therapeutic windows for "
        "combination targeting. We profile 24 patient samples and identify "
        "subclonal populations with distinct response profiles."
    )
    page.insert_text((72, 72), f"doi.org/10.1038/s41592-uptest-1\n{abstract}\nIntroduction follows.")
    doc.set_metadata({"title": "Embedded PDAC Study", "author": "Chen, Sarah"})
    out = doc.tobytes()
    doc.close()
    return out


@pytest.fixture
def fake_literature_bucket(monkeypatch):
    """Provision a Literature bucket for the success-path upload tests.

    Uploads now require a provisioned bucket; tests don't run a real GCS, so
    this stubs the bucket lookup and the GCS writes. New rejection tests omit
    this fixture (or override the bucket back to None) to exercise the
    no-storage path."""
    from app.services.literature import storage, upload_service

    async def fake_bucket(_session):
        return "bioaf-literature-test"

    async def fake_pdf_upload(_session, *, paper_id, pdf_bytes):
        return f"gs://bioaf-literature-test/papers/{paper_id}/original.pdf"

    async def fake_text_upload(_session, *, paper_id, text):
        return None

    monkeypatch.setattr(storage, "get_literature_bucket", fake_bucket)
    monkeypatch.setattr(upload_service, "upload_pdf_to_gcs", fake_pdf_upload)
    monkeypatch.setattr(upload_service, "upload_extracted_text_to_gcs", fake_text_upload)


@pytest.mark.asyncio
async def test_upload_rejected_when_no_literature_bucket(client, admin_token):
    """With no Literature bucket provisioned, an upload must fail loudly and
    create no paper, rather than appearing to succeed with nowhere to store
    the file."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    pdf = _build_test_pdf()
    files = {"file": ("paper.pdf", pdf, "application/pdf")}
    data = {"title": "Should Not Persist 8f3a"}
    resp = await client.post("/api/literature/papers/upload", files=files, data=data, headers=headers)
    assert resp.status_code == 503, resp.text
    assert "storage" in resp.json()["detail"].lower()

    listing = await client.get("/api/literature/papers", headers=headers)
    assert all(p["title"] != "Should Not Persist 8f3a" for p in listing.json()["items"])


@pytest.mark.asyncio
async def test_attach_pdf_rejected_when_no_literature_bucket(client, admin_token):
    """Attaching a PDF to an existing paper is rejected when storage is not
    provisioned; the paper is left unchanged (no stored PDF)."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "Abstract only", "authors": [{"given": "A", "family": "B"}], "doi": "10.nb/1"},
        headers=headers,
    )
    pid = r.json()["id"]
    pdf = _build_test_pdf()
    files = {"file": ("full.pdf", pdf, "application/pdf")}
    resp = await client.post(f"/api/literature/papers/{pid}/upload-pdf", files=files, headers=headers)
    assert resp.status_code == 503, resp.text

    after = await client.get(f"/api/literature/papers/{pid}", headers=headers)
    assert after.json()["has_pdf"] is False


@pytest.mark.asyncio
async def test_upload_pdf_creates_paper_with_extracted_metadata(client, admin_token, fake_literature_bucket):
    headers = {"Authorization": f"Bearer {admin_token}"}
    pdf = _build_test_pdf()
    files = {"file": ("paper.pdf", pdf, "application/pdf")}
    resp = await client.post("/api/literature/papers/upload", files=files, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Embedded PDAC Study"
    assert body["doi"] == "10.1038/s41592-uptest-1"
    assert body["authors"][0]["family"] == "Chen"
    assert body["extraction_status"] in {"pending", "complete"}


@pytest.mark.asyncio
async def test_upload_attaches_pdf_to_existing_library_paper(client, admin_token, fake_literature_bucket):
    """Uploading a paper that already exists in the Library (matched by DOI,
    e.g. previously added from a search or AI recommendation) attaches the PDF
    to that entry instead of dropping the file.

    Regression: the duplicate branch used to return the existing paper with no
    PDF stored, so the upload "succeeded" but no file was saved."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    # Pre-existing abstract-only entry sharing the DOI the test PDF embeds.
    r = await client.post(
        "/api/literature/papers",
        json={
            "title": "Embedded PDAC Study",
            "authors": [{"given": "Sarah", "family": "Chen"}],
            "doi": "10.1038/s41592-uptest-1",
        },
        headers=headers,
    )
    pid = r.json()["id"]
    assert r.json()["has_pdf"] is False

    pdf = _build_test_pdf()
    files = {"file": ("full.pdf", pdf, "application/pdf")}
    resp = await client.post("/api/literature/papers/upload", files=files, headers=headers)
    # Duplicate detected: returns the existing entry (200), now with the PDF.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    assert body["has_pdf"] is True
    assert body["in_library"] is True


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.post(
        "/api/literature/papers/upload",
        files={"file": ("paper.txt", b"hello", "text/plain")},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_uses_explicit_metadata_over_extracted(client, admin_token, fake_literature_bucket):
    headers = {"Authorization": f"Bearer {admin_token}"}
    pdf = _build_test_pdf()
    files = {"file": ("paper.pdf", pdf, "application/pdf")}
    data = {"title": "Override Title", "doi": "10.over/ride"}
    resp = await client.post(
        "/api/literature/papers/upload",
        files=files,
        data=data,
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Override Title"
    assert body["doi"] == "10.over/ride"


@pytest.mark.asyncio
async def test_re_extract_returns_404_when_no_pdf(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "no-pdf", "authors": [{"given": "A", "family": "B"}], "doi": "10.np/1"},
        headers=headers,
    )
    pid = r.json()["id"]
    r2 = await client.post(f"/api/literature/papers/{pid}/extract", headers=headers)
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_upload_pdf_to_existing_paper_without_conflict(client, admin_token, fake_literature_bucket):
    """Attaching a PDF to an abstract-only paper upgrades it in place: no
    second row, extraction queued, DOI backfilled from the PDF if absent."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    # Abstract-only entry with no DOI yet.
    r = await client.post(
        "/api/literature/papers",
        json={"title": "Abstract only", "authors": [{"given": "A", "family": "B"}]},
        headers=headers,
    )
    pid = r.json()["id"]

    pdf = _build_test_pdf()
    files = {"file": ("full.pdf", pdf, "application/pdf")}
    resp = await client.post(f"/api/literature/papers/{pid}/upload-pdf", files=files, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    # DOI extracted from the PDF backfills the entry.
    assert body["doi"] == "10.1038/s41592-uptest-1"
    assert body["extraction_status"] in {"pending", "complete"}


@pytest.mark.asyncio
async def test_upload_pdf_doi_conflict_prompts_then_merges(
    client, admin_token, admin_user, session, fake_literature_bucket
):
    """When the uploaded PDF's DOI matches a different existing paper, the
    first call returns 409 with the other paper; confirming the merge moves
    that paper's comments and AI notes onto the target and deletes the
    duplicate."""
    from sqlalchemy import text

    headers = {"Authorization": f"Bearer {admin_token}"}

    # Paper B already holds the PDF's DOI and carries a comment + an AI note.
    rb = await client.post(
        "/api/literature/papers",
        json={
            "title": "Existing abstract-only",
            "authors": [{"given": "Sarah", "family": "Chen"}],
            "doi": "10.1038/s41592-uptest-1",
        },
        headers=headers,
    )
    b_id = rb.json()["id"]
    cmt = await client.post(
        f"/api/literature/papers/{b_id}/comments",
        json={"body": "Sarah's note on the duplicate"},
        headers=headers,
    )
    assert cmt.status_code == 201

    exp = await session.execute(
        text(
            "INSERT INTO experiments (name, status, organization_id, owner_user_id, project_id) "
            "VALUES ('E', 'registered', :org, :uid, NULL) RETURNING id"
        ).bindparams(org=admin_user.organization_id, uid=admin_user.id)
    )
    experiment_id = exp.scalar_one()
    run = await session.execute(
        text(
            "INSERT INTO literature_review_runs "
            "(organization_id, experiment_id, triggered_by_user_id, status, llm_provider, llm_model) "
            "VALUES (:org, :eid, :uid, 'complete', 'anthropic', 'claude') RETURNING id"
        ).bindparams(org=admin_user.organization_id, eid=experiment_id, uid=admin_user.id)
    )
    run_id = run.scalar_one()
    await session.execute(
        text(
            "INSERT INTO literature_recommendations "
            "(organization_id, paper_id, experiment_id, review_run_id, relevance_score, relevance_bucket, status) "
            "VALUES (:org, :pid, :eid, :rid, 0.8, 'high', 'accepted')"
        ).bindparams(org=admin_user.organization_id, pid=b_id, eid=experiment_id, rid=run_id)
    )
    await session.commit()

    # Paper A is the target the user is uploading to: no DOI of its own.
    ra = await client.post(
        "/api/literature/papers",
        json={"title": "Target entry", "authors": [{"given": "Sarah", "family": "Chen"}]},
        headers=headers,
    )
    a_id = ra.json()["id"]

    pdf = _build_test_pdf()  # carries DOI 10.1038/s41592-uptest-1

    # First call (no confirmation) surfaces the conflict.
    files = {"file": ("full.pdf", pdf, "application/pdf")}
    conflict = await client.post(f"/api/literature/papers/{a_id}/upload-pdf", files=files, headers=headers)
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["other_paper_id"] == b_id
    assert "Existing abstract-only" in detail["other_paper_title"]

    # Confirming the merge folds B into A and attaches the PDF.
    files = {"file": ("full.pdf", pdf, "application/pdf")}
    merged = await client.post(
        f"/api/literature/papers/{a_id}/upload-pdf?confirm_merge=true",
        files=files,
        headers=headers,
    )
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["id"] == a_id
    assert body["doi"] == "10.1038/s41592-uptest-1"

    # B's comment is now on A.
    a_comments = await client.get(f"/api/literature/papers/{a_id}/comments", headers=headers)
    bodies = [c["body"] for c in a_comments.json()["items"]]
    assert "Sarah's note on the duplicate" in bodies

    # B's AI note is now on A.
    a_notes = await client.get(f"/api/literature/papers/{a_id}/recommendation-notes", headers=headers)
    assert any(n["experiment_id"] == experiment_id for n in a_notes.json())

    # B is gone.
    gone = await client.get(f"/api/literature/papers/{b_id}", headers=headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_delete_paper_files_targets_paper_prefix(session, monkeypatch):
    """delete_paper_files runs a prefix-delete pass over papers/{id}/ when a
    Literature bucket is provisioned.

    Routes through the BAL storage adapter (Phase 3): list_objects under the
    paper prefix, then delete each. Test mocks the adapter boundary."""
    from unittest.mock import AsyncMock, patch

    from app.adapters.models import StoredObject
    from app.services.literature import storage, upload_service

    async def fake_bucket(_s):
        return "test-bucket"

    monkeypatch.setattr(storage, "get_literature_bucket", fake_bucket)

    adapter = AsyncMock()
    adapter.list_objects.return_value = [
        StoredObject(filename="a.pdf", storage_uri="gs://test-bucket/papers/123/a.pdf"),
        StoredObject(filename="b.txt", storage_uri="gs://test-bucket/papers/123/b.txt"),
    ]

    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        ran = await upload_service.delete_paper_files(session, paper_id=123)

    assert ran is True
    adapter.list_objects.assert_awaited_once_with("gs://test-bucket/papers/123/")
    deleted = {call.args[0] for call in adapter.delete.await_args_list}
    assert deleted == {
        "gs://test-bucket/papers/123/a.pdf",
        "gs://test-bucket/papers/123/b.txt",
    }


@pytest.mark.asyncio
async def test_delete_paper_files_noop_without_bucket(session, monkeypatch):
    from app.services.literature import storage, upload_service

    async def fake_bucket(_s):
        return None

    monkeypatch.setattr(storage, "get_literature_bucket", fake_bucket)
    ran = await upload_service.delete_paper_files(session, paper_id=1)
    assert ran is False
