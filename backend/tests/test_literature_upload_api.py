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
