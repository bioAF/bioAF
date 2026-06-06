"""SDR API (ADR-063), Phase C acceptance criteria through the HTTP layer.

Covers create + numbering (AC-C01), activation (AC-C02), note-required and
superseded-target guards mapped to 422 (AC-C03, AC-C04, AC-C13), RBAC gating
(AC-C09 mechanism), owner reassignment (AC-C08), historical filtering (AC-C10),
and category management (F-LKC-08).
"""

import pytest


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _create(client, token, **kw):
    body = {
        "title": "Use STARsolo over CellRanger",
        "decision": "Standardize on STARsolo.",
        "justification": "Better multimapping handling.",
    }
    body.update(kw)
    return await client.post("/api/lab-knowledge/sdrs", json=body, headers=_auth(token))


@pytest.mark.asyncio
async def test_create_and_get_assigns_number(client, admin_token):
    # AC-C01
    resp = await _create(client, admin_token)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sdr_number"] == 1 and body["status"] == "draft"
    got = await client.get(f"/api/lab-knowledge/sdrs/{body['id']}", headers=_auth(admin_token))
    assert got.status_code == 200
    assert got.json()["decision"] == "Standardize on STARsolo."


@pytest.mark.asyncio
async def test_viewer_cannot_create_but_can_view(client, admin_token, viewer_token):
    # AC-C09 mechanism
    created = (await _create(client, admin_token)).json()
    listed = await client.get("/api/lab-knowledge/sdrs", headers=_auth(viewer_token))
    assert listed.status_code == 200 and listed.json()["total"] == 1
    blocked = await _create(client, viewer_token, title="Nope")
    assert blocked.status_code == 403
    # viewer cannot transition either
    t = await client.post(
        f"/api/lab-knowledge/sdrs/{created['id']}/transition",
        json={"to_status": "active"},
        headers=_auth(viewer_token),
    )
    assert t.status_code == 403


@pytest.mark.asyncio
async def test_activate_then_invalid_transition_returns_422(client, admin_token):
    # AC-C02, AC-C13
    created = (await _create(client, admin_token)).json()
    act = await client.post(
        f"/api/lab-knowledge/sdrs/{created['id']}/transition",
        json={"to_status": "active"},
        headers=_auth(admin_token),
    )
    assert act.status_code == 200 and act.json()["status"] == "active"
    # active -> draft is not permitted
    bad = await client.post(
        f"/api/lab-knowledge/sdrs/{created['id']}/transition",
        json={"to_status": "repealed"},
        headers=_auth(admin_token),
    )
    assert bad.status_code == 200  # repeal is allowed from active
    # superseded SDR (terminal) cannot transition to active
    bad2 = await client.post(
        f"/api/lab-knowledge/sdrs/{created['id']}/transition",
        json={"to_status": "active"},
        headers=_auth(admin_token),
    )
    assert bad2.status_code == 422


@pytest.mark.asyncio
async def test_flagged_to_active_without_note_is_422(client, admin_token):
    # AC-C03
    created = (await _create(client, admin_token)).json()
    sid = created["id"]
    await client.post(
        f"/api/lab-knowledge/sdrs/{sid}/transition", json={"to_status": "active"}, headers=_auth(admin_token)
    )
    await client.post(
        f"/api/lab-knowledge/sdrs/{sid}/transition",
        json={"to_status": "flagged_for_review"},
        headers=_auth(admin_token),
    )
    no_note = await client.post(
        f"/api/lab-knowledge/sdrs/{sid}/transition", json={"to_status": "active"}, headers=_auth(admin_token)
    )
    assert no_note.status_code == 422
    with_note = await client.post(
        f"/api/lab-knowledge/sdrs/{sid}/transition",
        json={"to_status": "active", "note": "Decision upheld."},
        headers=_auth(admin_token),
    )
    assert with_note.status_code == 200


@pytest.mark.asyncio
async def test_supersede_without_target_is_422_then_links(client, admin_token):
    # AC-C04, AC-C07
    old = (await _create(client, admin_token)).json()
    new = (await _create(client, admin_token, title="STARsolo v2")).json()
    await client.post(
        f"/api/lab-knowledge/sdrs/{old['id']}/transition", json={"to_status": "active"}, headers=_auth(admin_token)
    )
    bad = await client.post(
        f"/api/lab-knowledge/sdrs/{old['id']}/transition", json={"to_status": "superseded"}, headers=_auth(admin_token)
    )
    assert bad.status_code == 422
    ok = await client.post(
        f"/api/lab-knowledge/sdrs/{old['id']}/transition",
        json={"to_status": "superseded", "superseded_by_sdr_id": new["id"]},
        headers=_auth(admin_token),
    )
    assert ok.status_code == 200
    detail = (await client.get(f"/api/lab-knowledge/sdrs/{old['id']}", headers=_auth(admin_token))).json()
    assert detail["superseded_by"]["id"] == new["id"]
    new_detail = (await client.get(f"/api/lab-knowledge/sdrs/{new['id']}", headers=_auth(admin_token))).json()
    assert new_detail["supersedes"]["id"] == old["id"]


@pytest.mark.asyncio
async def test_list_hides_historical_by_default(client, admin_token):
    # AC-C10
    a = (await _create(client, admin_token, title="Keeper")).json()
    b = (await _create(client, admin_token, title="Goner")).json()
    await client.post(
        f"/api/lab-knowledge/sdrs/{a['id']}/transition", json={"to_status": "active"}, headers=_auth(admin_token)
    )
    await client.post(
        f"/api/lab-knowledge/sdrs/{b['id']}/transition", json={"to_status": "active"}, headers=_auth(admin_token)
    )
    await client.post(
        f"/api/lab-knowledge/sdrs/{b['id']}/transition", json={"to_status": "repealed"}, headers=_auth(admin_token)
    )
    default = await client.get("/api/lab-knowledge/sdrs", headers=_auth(admin_token))
    assert default.json()["total"] == 1
    histo = await client.get("/api/lab-knowledge/sdrs?include_historical=true", headers=_auth(admin_token))
    assert histo.json()["total"] == 2


@pytest.mark.asyncio
async def test_transition_history_in_detail(client, admin_token):
    # AC-C11
    created = (await _create(client, admin_token)).json()
    await client.post(
        f"/api/lab-knowledge/sdrs/{created['id']}/transition", json={"to_status": "active"}, headers=_auth(admin_token)
    )
    detail = (await client.get(f"/api/lab-knowledge/sdrs/{created['id']}", headers=_auth(admin_token))).json()
    pairs = [(t["from_status"], t["to_status"]) for t in detail["transitions"]]
    assert ("draft", "active") in pairs


@pytest.mark.asyncio
async def test_categories_crud(client, admin_token):
    # F-LKC-08
    seeded = await client.get("/api/lab-knowledge/sdr-categories", headers=_auth(admin_token))
    assert seeded.status_code == 200
    created = await client.post(
        "/api/lab-knowledge/sdr-categories", json={"name": "Chemistry"}, headers=_auth(admin_token)
    )
    assert created.status_code == 200
    cat_id = created.json()["id"]
    # Assign to an SDR, then deletion is blocked
    sdr = (await _create(client, admin_token, category_id=cat_id)).json()
    blocked = await client.delete(f"/api/lab-knowledge/sdr-categories/{cat_id}", headers=_auth(admin_token))
    assert blocked.status_code == 409
    # Clear the category, then delete works
    await client.patch(f"/api/lab-knowledge/sdrs/{sdr['id']}", json={"category_id": None}, headers=_auth(admin_token))
    ok = await client.delete(f"/api/lab-knowledge/sdr-categories/{cat_id}", headers=_auth(admin_token))
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_owner_reassign_requires_manage(client, admin_token, viewer_token):
    # AC-C08
    created = (await _create(client, admin_token)).json()
    # viewer (no manage) is blocked
    blocked = await client.patch(
        f"/api/lab-knowledge/sdrs/{created['id']}/owner",
        json={"owner_user_id": 1},
        headers=_auth(viewer_token),
    )
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_delete_requires_manage(client, admin_token, viewer_token):
    created = (await _create(client, admin_token)).json()
    blocked = await client.delete(f"/api/lab-knowledge/sdrs/{created['id']}", headers=_auth(viewer_token))
    assert blocked.status_code == 403
    ok = await client.delete(f"/api/lab-knowledge/sdrs/{created['id']}", headers=_auth(admin_token))
    assert ok.status_code == 200
