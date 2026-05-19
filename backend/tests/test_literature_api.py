"""Library API tests: papers, comments, associations, reading status,
dismissal, citation export.

Uses the same ASGI fixtures as the rest of the suite (client, admin_token,
viewer_token). Org isolation, permission gating, and dedup semantics are
the load-bearing things checked here."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_admin_can_create_and_list_paper(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.post(
        "/api/literature/papers",
        json={
            "title": "Tumour heterogeneity in PDAC",
            "authors": [{"given": "Sarah", "family": "Chen"}],
            "doi": "10.1000/abc",
            "journal": "Nature Methods",
            "abstract": "An abstract.",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Tumour heterogeneity in PDAC"
    assert body["doi"] == "10.1000/abc"
    assert body["comment_count"] == 0
    assert body["dismissed"] is False
    paper_id = body["id"]

    resp = await client.get("/api/literature/papers", headers=headers)
    assert resp.status_code == 200
    lst = resp.json()
    assert lst["total"] >= 1
    assert any(p["id"] == paper_id for p in lst["items"])


@pytest.mark.asyncio
async def test_viewer_cannot_create_paper(client, viewer_token):
    headers = {"Authorization": f"Bearer {viewer_token}"}
    resp = await client.post(
        "/api/literature/papers",
        json={"title": "x", "authors": [{"given": "A", "family": "B"}], "doi": "10.x/y"},
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_doi_returns_existing(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    payload = {
        "title": "Dup Title",
        "authors": [{"given": "A", "family": "B"}],
        "doi": "10.dup/x",
    }
    r1 = await client.post("/api/literature/papers", json=payload, headers=headers)
    assert r1.status_code == 201
    r2 = await client.post("/api/literature/papers", json=payload, headers=headers)
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_fallback_dedup_by_title_and_authors(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    payload = {
        "title": "No DOI Paper",
        "authors": [{"given": "Brent", "family": "Mills"}],
    }
    r1 = await client.post("/api/literature/papers", json=payload, headers=headers)
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/literature/papers",
        json={"title": "no  doi    paper!", "authors": [{"given": "Brent", "family": "Mills"}]},
        headers=headers,
    )
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_paper_update_and_audit(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r1 = await client.post(
        "/api/literature/papers",
        json={"title": "Old", "authors": [{"given": "A", "family": "B"}], "doi": "10.u/1"},
        headers=headers,
    )
    pid = r1.json()["id"]
    r2 = await client.patch(
        f"/api/literature/papers/{pid}", json={"title": "New"}, headers=headers
    )
    assert r2.status_code == 200
    assert r2.json()["title"] == "New"


@pytest.mark.asyncio
async def test_paper_delete(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r1 = await client.post(
        "/api/literature/papers",
        json={"title": "To delete", "authors": [{"given": "A", "family": "B"}], "doi": "10.d/1"},
        headers=headers,
    )
    pid = r1.json()["id"]
    r2 = await client.delete(f"/api/literature/papers/{pid}", headers=headers)
    assert r2.status_code == 204
    r3 = await client.get(f"/api/literature/papers/{pid}", headers=headers)
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_associations_create_and_remove(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "Assoc", "authors": [{"given": "A", "family": "B"}], "doi": "10.assoc/1"},
        headers=headers,
    )
    pid = r.json()["id"]
    r2 = await client.post(
        f"/api/literature/papers/{pid}/associations",
        json={"scope_type": "global"},
        headers=headers,
    )
    assert r2.status_code == 200
    aid = r2.json()["id"]
    # Same scope: get_or_create returns existing.
    r3 = await client.post(
        f"/api/literature/papers/{pid}/associations",
        json={"scope_type": "global"},
        headers=headers,
    )
    assert r3.json()["id"] == aid
    r4 = await client.delete(
        f"/api/literature/papers/{pid}/associations/{aid}", headers=headers
    )
    assert r4.status_code == 204


@pytest.mark.asyncio
async def test_comments_create_reply_delete(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "C", "authors": [{"given": "A", "family": "B"}], "doi": "10.c/1"},
        headers=headers,
    )
    pid = r.json()["id"]
    r1 = await client.post(
        f"/api/literature/papers/{pid}/comments",
        json={"body": "top"},
        headers=headers,
    )
    assert r1.status_code == 201
    top_id = r1.json()["id"]
    r2 = await client.post(
        f"/api/literature/papers/{pid}/comments",
        json={"body": "reply", "parent_id": top_id},
        headers=headers,
    )
    assert r2.status_code == 201
    r3 = await client.get(f"/api/literature/papers/{pid}/comments", headers=headers)
    items = r3.json()["items"]
    assert len(items) == 2

    # delete reply (own comment) -> body becomes None
    reply_id = r2.json()["id"]
    r4 = await client.delete(f"/api/literature/comments/{reply_id}", headers=headers)
    assert r4.status_code == 204
    r5 = await client.get(f"/api/literature/papers/{pid}/comments", headers=headers)
    items5 = r5.json()["items"]
    deleted = next(c for c in items5 if c["id"] == reply_id)
    assert deleted["deleted"] is True
    assert deleted["body"] is None


@pytest.mark.asyncio
async def test_reading_status_default_and_set(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "R", "authors": [{"given": "A", "family": "B"}], "doi": "10.r/1"},
        headers=headers,
    )
    pid = r.json()["id"]
    r1 = await client.get(f"/api/literature/papers/{pid}/reading-status", headers=headers)
    assert r1.json()["status"] == "unread"
    r2 = await client.put(
        f"/api/literature/papers/{pid}/reading-status",
        json={"status": "reading"},
        headers=headers,
    )
    assert r2.json()["status"] == "reading"


@pytest.mark.asyncio
async def test_dismissal_and_reverse(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "D", "authors": [{"given": "A", "family": "B"}], "doi": "10.dx/1"},
        headers=headers,
    )
    pid = r.json()["id"]
    r1 = await client.post(
        f"/api/literature/papers/{pid}/dismiss",
        json={"reason": "off topic"},
        headers=headers,
    )
    assert r1.status_code == 200
    assert r1.json()["reason"] == "off topic"

    # Default list hides dismissed papers.
    lst = await client.get("/api/literature/papers", headers=headers)
    assert all(p["id"] != pid for p in lst.json()["items"])

    # show_dismissed surfaces them.
    lst2 = await client.get("/api/literature/papers?show_dismissed=true", headers=headers)
    assert any(p["id"] == pid for p in lst2.json()["items"])

    # Admin reverses.
    r2 = await client.post(
        f"/api/literature/papers/{pid}/dismiss/reverse", headers=headers
    )
    assert r2.status_code == 200
    assert r2.json()["reversed_at"] is not None


@pytest.mark.asyncio
async def test_dismissal_excludes_from_default_list(client, admin_token):
    """Confirm the default list view does not surface dismissed papers."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    r1 = await client.post(
        "/api/literature/papers",
        json={"title": "ListA", "authors": [{"given": "A", "family": "B"}], "doi": "10.la/1"},
        headers=headers,
    )
    a_id = r1.json()["id"]
    r2 = await client.post(
        "/api/literature/papers",
        json={"title": "ListB", "authors": [{"given": "C", "family": "D"}], "doi": "10.la/2"},
        headers=headers,
    )
    b_id = r2.json()["id"]
    await client.post(f"/api/literature/papers/{a_id}/dismiss", json={}, headers=headers)
    lst = await client.get("/api/literature/papers", headers=headers)
    ids = [p["id"] for p in lst.json()["items"]]
    assert a_id not in ids
    assert b_id in ids


@pytest.mark.asyncio
async def test_citation_export_bibtex_and_ris(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={
            "title": "Citable",
            "authors": [{"given": "Sarah", "family": "Chen"}],
            "doi": "10.cite/1",
            "journal": "Nature",
            "publication_date": "2024-05-12",
        },
        headers=headers,
    )
    pid = r.json()["id"]
    rb = await client.get(f"/api/literature/papers/{pid}/citation?format=bibtex", headers=headers)
    text_b = rb.text
    assert "@article" in text_b
    assert "Chen, Sarah" in text_b
    assert "10.cite/1" in text_b
    rr = await client.get(f"/api/literature/papers/{pid}/citation?format=ris", headers=headers)
    text_r = rr.text
    assert "TY  - JOUR" in text_r
    assert "AU  - Chen, Sarah" in text_r
    assert "ER  - " in text_r


@pytest.mark.asyncio
async def test_viewer_cannot_comment(client, viewer_token, admin_token):
    headers_admin = {"Authorization": f"Bearer {admin_token}"}
    headers_viewer = {"Authorization": f"Bearer {viewer_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "V", "authors": [{"given": "A", "family": "B"}], "doi": "10.v/1"},
        headers=headers_admin,
    )
    pid = r.json()["id"]
    r2 = await client.post(
        f"/api/literature/papers/{pid}/comments",
        json={"body": "hello"},
        headers=headers_viewer,
    )
    assert r2.status_code == 403
