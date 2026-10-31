"""Regression cover for attaching a PDF to an existing paper (manual upload),
pinned before the attach path was shared with open-access fetches."""

from __future__ import annotations

import pytest

from tests.test_literature_upload_api import _build_test_pdf, fake_literature_bucket  # noqa: F401


@pytest.fixture
def scheduled(monkeypatch):
    from app.services.literature import upload_service

    calls: list[dict] = []

    async def fake_schedule(*, paper_id, pdf_bytes, user_id, api_key_id=None):
        calls.append({"paper_id": paper_id, "size": len(pdf_bytes)})

    monkeypatch.setattr(upload_service, "schedule_extraction", fake_schedule)
    return calls


async def _paper(client, headers, **fields) -> int:
    r = await client.post(
        "/api/literature/papers",
        json={"title": "Target", "authors": [{"given": "A", "family": "B"}], **fields},
        headers=headers,
    )
    return r.json()["id"]


@pytest.mark.asyncio
async def test_attach_schedules_extraction_for_that_paper(client, admin_token, fake_literature_bucket, scheduled):  # noqa: F811
    headers = {"Authorization": f"Bearer {admin_token}"}
    pid = await _paper(client, headers)
    pdf = _build_test_pdf()

    r = await client.post(
        f"/api/literature/papers/{pid}/upload-pdf", files={"file": ("a.pdf", pdf, "application/pdf")}, headers=headers
    )

    assert r.status_code == 200, r.text
    assert r.json()["has_pdf"] is True
    assert r.json()["extraction_status"] == "pending"
    assert scheduled == [{"paper_id": pid, "size": len(pdf)}]


@pytest.mark.asyncio
async def test_attach_replaces_an_existing_pdf(client, admin_token, fake_literature_bucket, scheduled):  # noqa: F811
    headers = {"Authorization": f"Bearer {admin_token}"}
    pid = await _paper(client, headers)
    pdf = _build_test_pdf()
    for _ in range(2):
        r = await client.post(
            f"/api/literature/papers/{pid}/upload-pdf",
            files={"file": ("a.pdf", pdf, "application/pdf")},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    assert r.json()["id"] == pid
    assert len(scheduled) == 2


@pytest.mark.asyncio
async def test_attach_keeps_the_papers_own_doi(client, admin_token, fake_literature_bucket, scheduled):  # noqa: F811
    headers = {"Authorization": f"Bearer {admin_token}"}
    pid = await _paper(client, headers, doi="10.1/own")

    r = await client.post(
        f"/api/literature/papers/{pid}/upload-pdf",
        files={"file": ("a.pdf", _build_test_pdf(), "application/pdf")},
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["doi"] == "10.1/own"


@pytest.mark.asyncio
async def test_attach_conflict_without_confirmation_changes_nothing(
    client,
    admin_token,
    fake_literature_bucket,  # noqa: F811
    scheduled,
):
    headers = {"Authorization": f"Bearer {admin_token}"}
    other = await _paper(client, headers, title="Holder", doi="10.1038/s41592-uptest-1")
    pid = await _paper(client, headers)

    r = await client.post(
        f"/api/literature/papers/{pid}/upload-pdf",
        files={"file": ("a.pdf", _build_test_pdf(), "application/pdf")},
        headers=headers,
    )

    assert r.status_code == 409
    after = (await client.get(f"/api/literature/papers/{pid}", headers=headers)).json()
    assert after["has_pdf"] is False and after["doi"] is None
    assert (await client.get(f"/api/literature/papers/{other}", headers=headers)).status_code == 200
    assert scheduled == []
