"""Lab Glossary API (ADR-062), Phase B acceptance criteria.

Exercises term CRUD + RBAC, duplicate handling, CSV import + review, and the
pending-review surface through the HTTP layer. Covers AC-B01..B04, AC-B10..B12.
"""

import io

import pytest


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _create_term(client, token, *, term="Oocyte", definition="An immature egg cell."):
    return await client.post(
        "/api/lab-knowledge/glossary",
        json={"term": term, "definition": definition},
        headers=_auth(token),
    )


@pytest.mark.asyncio
async def test_create_and_list_term(client, admin_token):
    # AC-B01
    resp = await _create_term(client, admin_token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "manual"
    listed = await client.get("/api/lab-knowledge/glossary", headers=_auth(admin_token))
    assert listed.json()["total"] == 1
    assert listed.json()["terms"][0]["term"] == "Oocyte"


@pytest.mark.asyncio
async def test_viewer_can_view_cannot_create(client, admin_token, viewer_token):
    await _create_term(client, admin_token)
    listed = await client.get("/api/lab-knowledge/glossary", headers=_auth(viewer_token))
    assert listed.status_code == 200 and listed.json()["total"] == 1
    blocked = await _create_term(client, viewer_token, term="Spheroid", definition="3D cluster")
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_term_returns_409(client, admin_token):
    # AC-B02
    await _create_term(client, admin_token, term="Passage 3", definition="3rd passage")
    dup = await _create_term(client, admin_token, term="passage 3", definition="dup")
    assert dup.status_code == 409
    assert dup.json()["detail"]["error"] == "duplicate_term"


@pytest.mark.asyncio
async def test_update_term(client, admin_token):
    created = (await _create_term(client, admin_token)).json()
    resp = await client.patch(
        f"/api/lab-knowledge/glossary/{created['id']}",
        json={"definition": "Updated definition"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200 and resp.json()["definition"] == "Updated definition"


@pytest.mark.asyncio
async def test_delete_term_admin_only(client, admin_token, viewer_token):
    # AC-B11
    created = (await _create_term(client, admin_token)).json()
    blocked = await client.delete(
        f"/api/lab-knowledge/glossary/{created['id']}", headers=_auth(viewer_token)
    )
    assert blocked.status_code == 403
    ok = await client.delete(
        f"/api/lab-knowledge/glossary/{created['id']}", headers=_auth(admin_token)
    )
    assert ok.status_code == 200
    listed = await client.get("/api/lab-knowledge/glossary", headers=_auth(admin_token))
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
async def test_csv_import_then_review_accept_all(client, admin_token):
    # AC-B03, AC-B10
    csv = b"term,definition,aliases\nGastruloid,A 3D embryo model,GLD\nSpheroid,A 3D cell cluster,\n"
    files = {"file": ("terms.csv", io.BytesIO(csv), "text/csv")}
    resp = await client.post("/api/lab-knowledge/glossary/import", files=files, headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["id"]

    props = await client.get(
        f"/api/lab-knowledge/glossary/scan/{job_id}/proposals", headers=_auth(admin_token)
    )
    assert len(props.json()["new_terms"]) == 2

    review = await client.post(
        f"/api/lab-knowledge/glossary/scan/{job_id}/review",
        json={"accept_all_remaining": True},
        headers=_auth(admin_token),
    )
    assert review.status_code == 200 and review.json()["accepted"] == 2

    listed = await client.get("/api/lab-knowledge/glossary", headers=_auth(admin_token))
    assert listed.json()["total"] == 2
    assert all(t["source"] == "import" for t in listed.json()["terms"])


@pytest.mark.asyncio
async def test_csv_import_missing_column_returns_400(client, admin_token):
    # AC-B04
    csv = b"term,notes\nFoo,bar\n"
    files = {"file": ("bad.csv", io.BytesIO(csv), "text/csv")}
    resp = await client.post("/api/lab-knowledge/glossary/import", files=files, headers=_auth(admin_token))
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "csv_parse_error"


@pytest.mark.asyncio
async def test_pending_review_count_reflects_unreviewed(client, admin_token):
    csv = b"term,definition\nA,da\nB,db\n"
    files = {"file": ("t.csv", io.BytesIO(csv), "text/csv")}
    await client.post("/api/lab-knowledge/glossary/import", files=files, headers=_auth(admin_token))
    pending = await client.get("/api/lab-knowledge/glossary/pending", headers=_auth(admin_token))
    assert pending.json()["pending_review_count"] == 2
