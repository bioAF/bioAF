"""Where a paper's stored full text came from: version, source, copy URL, time.
Set only for an attached open-access copy; null for everything else."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.test_literature_upload_api import _build_test_pdf, fake_literature_bucket  # noqa: F401

_PROVENANCE = {
    "full_text_version": "acceptedVersion",
    "full_text_source": "openalex",
    "full_text_source_url": "https://repo.example/accepted.pdf",
    "full_text_acquired_at": datetime(2026, 10, 1, tzinfo=UTC),
}


async def _paper_with_provenance(session, org_id, user_id, **fields):
    from app.services.literature import paper_service

    paper = await paper_service.create_paper(
        session, org_id=org_id, user_id=user_id, title=fields.pop("title", "P"), authors=[], **fields
    )
    paper.gcs_pdf_uri = "gs://bioaf-literature-test/papers/x/original.pdf"
    for k, v in _PROVENANCE.items():
        setattr(paper, k, v)
    await session.commit()
    return paper


@pytest.mark.asyncio
async def test_provenance_is_serialized(client, admin_token, admin_user, session):
    paper = await _paper_with_provenance(session, admin_user.organization_id, admin_user.id)

    body = (
        await client.get(f"/api/literature/papers/{paper.id}", headers={"Authorization": f"Bearer {admin_token}"})
    ).json()

    assert body["full_text_version"] == "acceptedVersion"
    assert body["full_text_source"] == "openalex"
    assert body["full_text_source_url"] == "https://repo.example/accepted.pdf"
    assert body["full_text_acquired_at"].startswith("2026-10-01")


@pytest.mark.asyncio
async def test_new_papers_have_no_provenance(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post("/api/literature/papers", json={"title": "Fresh", "authors": []}, headers=headers)

    body = r.json()
    assert body["full_text_version"] is None
    assert body["full_text_source"] is None
    assert body["full_text_source_url"] is None
    assert body["full_text_acquired_at"] is None


@pytest.mark.asyncio
async def test_manual_replacement_clears_provenance(
    client,
    admin_token,
    admin_user,
    session,
    fake_literature_bucket,  # noqa: F811
):
    paper = await _paper_with_provenance(session, admin_user.organization_id, admin_user.id)

    r = await client.post(
        f"/api/literature/papers/{paper.id}/upload-pdf",
        files={"file": ("mine.pdf", _build_test_pdf(), "application/pdf")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["full_text_version"] is None
    assert r.json()["full_text_source"] is None
    assert r.json()["full_text_source_url"] is None
    assert r.json()["full_text_acquired_at"] is None


@pytest.mark.asyncio
async def test_deleting_a_paper_clears_provenance(admin_user, session, monkeypatch):
    from app.services.literature import paper_service, upload_service

    async def no_files(_session, *, paper_id):
        return True

    monkeypatch.setattr(upload_service, "delete_paper_files", no_files)
    paper = await _paper_with_provenance(session, admin_user.organization_id, admin_user.id)

    await paper_service.delete_paper(session, paper=paper, user_id=admin_user.id)

    assert paper.full_text_version is None
    assert paper.full_text_source is None
    assert paper.full_text_source_url is None
    assert paper.full_text_acquired_at is None


@pytest.mark.asyncio
async def test_merge_keeps_the_survivors_provenance(admin_user, session):
    from app.services.literature import paper_service

    org, uid = admin_user.organization_id, admin_user.id
    survivor = await paper_service.create_paper(session, org_id=org, user_id=uid, title="Survivor", authors=[])
    await session.commit()
    duplicate = await _paper_with_provenance(session, org, uid, title="Duplicate")

    await paper_service.merge_papers(session, survivor=survivor, duplicate=duplicate, user_id=uid)

    assert survivor.full_text_version is None
    assert survivor.full_text_source is None


@pytest.mark.asyncio
async def test_upload_onto_an_existing_paper_clears_provenance(
    client,
    admin_token,
    admin_user,
    session,
    fake_literature_bucket,  # noqa: F811
):
    paper = await _paper_with_provenance(
        session, admin_user.organization_id, admin_user.id, doi="10.1038/s41592-uptest-1"
    )

    r = await client.post(
        "/api/literature/papers/upload",
        files={"file": ("mine.pdf", _build_test_pdf(), "application/pdf")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["id"] == paper.id
    assert r.json()["full_text_source"] is None
    assert r.json()["full_text_version"] is None
