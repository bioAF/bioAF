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
async def test_experiment_association_carries_parent_project_breadcrumb(
    client, admin_token, admin_user, session
):
    """An experiment-scope association should expose its parent project so
    the UI can render the breadcrumb Project > Experiment. A project-scope
    association does not need parent metadata."""
    from sqlalchemy import text

    proj = await session.execute(
        text(
            "INSERT INTO projects (name, organization_id, owner_user_id) "
            "VALUES ('Atlas of TGF-beta', :org, :uid) RETURNING id"
        ).bindparams(org=admin_user.organization_id, uid=admin_user.id)
    )
    project_id = proj.scalar_one()
    exp = await session.execute(
        text(
            "INSERT INTO experiments (name, status, organization_id, owner_user_id, project_id) "
            "VALUES ('Exp One', 'registered', :org, :uid, :pid) RETURNING id"
        ).bindparams(
            org=admin_user.organization_id,
            uid=admin_user.id,
            pid=project_id,
        )
    )
    experiment_id = exp.scalar_one()
    await session.commit()

    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "BC", "authors": [{"given": "A", "family": "B"}], "doi": "10.bc/1"},
        headers=headers,
    )
    pid = r.json()["id"]

    exp_assoc = await client.post(
        f"/api/literature/papers/{pid}/associations",
        json={"scope_type": "experiment", "scope_id": experiment_id},
        headers=headers,
    )
    assert exp_assoc.status_code == 200
    payload = exp_assoc.json()
    assert payload["scope_type"] == "experiment"
    assert payload["scope_id"] == experiment_id
    assert payload["scope_name"] == "Exp One"
    # New: parent project metadata travels with the experiment association.
    assert payload["parent_project_id"] == project_id
    assert payload["parent_project_name"] == "Atlas of TGF-beta"

    proj_assoc = await client.post(
        f"/api/literature/papers/{pid}/associations",
        json={"scope_type": "project", "scope_id": project_id},
        headers=headers,
    )
    proj_payload = proj_assoc.json()
    assert proj_payload["scope_type"] == "project"
    # No parent for project-scope rows.
    assert proj_payload.get("parent_project_id") is None
    assert proj_payload.get("parent_project_name") is None

    # The associations are also reflected on the paper response.
    paper = await client.get(f"/api/literature/papers/{pid}", headers=headers)
    by_scope = {a["scope_type"]: a for a in paper.json()["associations"]}
    assert by_scope["experiment"]["parent_project_name"] == "Atlas of TGF-beta"
    assert by_scope["project"]["parent_project_id"] is None


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
async def test_comment_response_includes_author_attribution(client, admin_token, admin_user):
    """Each comment payload exposes the author so the UI can attribute it.

    Without an attribution field the UI ends up showing "anonymous"
    timestamps for every reply, which is what the demo surfaced.
    """
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/literature/papers",
        json={"title": "CA", "authors": [{"given": "A", "family": "B"}], "doi": "10.attr/1"},
        headers=headers,
    )
    pid = r.json()["id"]
    rc = await client.post(
        f"/api/literature/papers/{pid}/comments",
        json={"body": "hello"},
        headers=headers,
    )
    assert rc.status_code == 201
    body = rc.json()
    assert body["user_id"] == admin_user.id
    # Author label resolves the user; falls back to email if name is unset.
    assert body.get("user_name") == (admin_user.name or admin_user.email)

    listing = await client.get(
        f"/api/literature/papers/{pid}/comments", headers=headers
    )
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["user_name"] == (admin_user.name or admin_user.email)


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

    # include_dismissed surfaces them.
    lst2 = await client.get(
        "/api/literature/papers?include_dismissed=true",
        headers=headers,
    )
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


@pytest.mark.asyncio
async def test_reading_status_toggle_filters(client, admin_token):
    """Verify the new reading_status[] list filter on /papers."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    ids = []
    for i, title in enumerate(["RS-A", "RS-B", "RS-C"]):
        r = await client.post(
            "/api/literature/papers",
            json={"title": title, "authors": [{"given": "X", "family": f"Y{i}"}], "doi": f"10.rs/{i}"},
            headers=headers,
        )
        ids.append(r.json()["id"])

    await client.put(
        f"/api/literature/papers/{ids[0]}/reading-status",
        json={"status": "reading"},
        headers=headers,
    )
    await client.put(
        f"/api/literature/papers/{ids[1]}/reading-status",
        json={"status": "read"},
        headers=headers,
    )
    # ids[2] stays unread (no row -> default unread)

    # reading_status=reading should yield only the first
    only_reading = await client.get(
        "/api/literature/papers?reading_status=reading", headers=headers
    )
    rs_ids = [p["id"] for p in only_reading.json()["items"]]
    assert ids[0] in rs_ids
    assert ids[1] not in rs_ids
    assert ids[2] not in rs_ids

    # reading_status=unread should include the third (no row) and exclude the others
    only_unread = await client.get(
        "/api/literature/papers?reading_status=unread", headers=headers
    )
    rs_ids = [p["id"] for p in only_unread.json()["items"]]
    assert ids[2] in rs_ids
    assert ids[0] not in rs_ids
    assert ids[1] not in rs_ids

    # reading_status=reading&reading_status=read yields the two with rows
    pair = await client.get(
        "/api/literature/papers?reading_status=reading&reading_status=read",
        headers=headers,
    )
    rs_ids = [p["id"] for p in pair.json()["items"]]
    assert ids[0] in rs_ids
    assert ids[1] in rs_ids
    assert ids[2] not in rs_ids


@pytest.mark.asyncio
async def test_lit_review_settings_default_and_update(client, admin_token, viewer_token):
    """The org's Lit Review relevance threshold defaults to 0.65 and is
    settable by admins between 0.0 and 1.0 inclusive."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.get(
        "/api/literature/settings/lit-review", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["relevance_threshold"] == 0.65

    upd = await client.put(
        "/api/literature/settings/lit-review",
        json={"relevance_threshold": 0.4},
        headers=headers,
    )
    assert upd.status_code == 200
    assert upd.json()["relevance_threshold"] == 0.4

    # Out of range rejected.
    bad = await client.put(
        "/api/literature/settings/lit-review",
        json={"relevance_threshold": 1.5},
        headers=headers,
    )
    assert bad.status_code == 400

    # Non-admin (viewer) cannot configure.
    v = await client.put(
        "/api/literature/settings/lit-review",
        json={"relevance_threshold": 0.5},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert v.status_code == 403


def test_sanitize_source_text_decodes_and_strips():
    """The source-text sanitizer decodes HTML entities and strips inline tags."""
    from app.services.literature.sources import sanitize_source_text

    raw = (
        "Stiff matrix promotes lung cancer cell migration through down-regulating "
        "the Piezo1 channel expression to facilitate Ca&lt;sup&gt;2+&lt;/sup&gt;"
        "-dependent filopodia formation."
    )
    cleaned = sanitize_source_text(raw)
    assert cleaned is not None
    assert "<" not in cleaned
    assert "&lt;" not in cleaned
    assert "Ca2+" in cleaned

    # Double-encoded payload should still come out clean.
    double = "alpha &amp;amp; beta &amp;lt;sub&amp;gt;x&amp;lt;/sub&amp;gt;"
    cleaned2 = sanitize_source_text(double)
    assert cleaned2 == "alpha & beta x"

    # Inline tags stripped.
    tagged = "<i>italic</i> & <sup>2+</sup> after"
    cleaned3 = sanitize_source_text(tagged)
    assert cleaned3 == "italic & 2+ after"

    assert sanitize_source_text(None) is None
    assert sanitize_source_text("") is None
