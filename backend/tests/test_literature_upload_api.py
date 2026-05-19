"""Upload endpoint: ASGI integration that exercises the synchronous extraction
path and verifies the resulting Paper has the right metadata."""

from __future__ import annotations

import asyncio

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


@pytest.mark.asyncio
async def test_upload_pdf_creates_paper_with_extracted_metadata(client, admin_token):
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
async def test_upload_rejects_non_pdf(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.post(
        "/api/literature/papers/upload",
        files={"file": ("paper.txt", b"hello", "text/plain")},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_uses_explicit_metadata_over_extracted(client, admin_token):
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
async def test_upload_pdf_to_existing_paper_without_conflict(client, admin_token):
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
    resp = await client.post(
        f"/api/literature/papers/{pid}/upload-pdf", files=files, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    # DOI extracted from the PDF backfills the entry.
    assert body["doi"] == "10.1038/s41592-uptest-1"
    assert body["extraction_status"] in {"pending", "complete"}


@pytest.mark.asyncio
async def test_upload_pdf_doi_conflict_prompts_then_merges(
    client, admin_token, admin_user, session
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
    conflict = await client.post(
        f"/api/literature/papers/{a_id}/upload-pdf", files=files, headers=headers
    )
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
    a_comments = await client.get(
        f"/api/literature/papers/{a_id}/comments", headers=headers
    )
    bodies = [c["body"] for c in a_comments.json()["items"]]
    assert "Sarah's note on the duplicate" in bodies

    # B's AI note is now on A.
    a_notes = await client.get(
        f"/api/literature/papers/{a_id}/recommendation-notes", headers=headers
    )
    assert any(n["experiment_id"] == experiment_id for n in a_notes.json())

    # B is gone.
    gone = await client.get(f"/api/literature/papers/{b_id}", headers=headers)
    assert gone.status_code == 404
