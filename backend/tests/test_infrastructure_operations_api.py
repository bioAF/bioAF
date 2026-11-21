"""Infrastructure operation routes (storage lifecycle 11D; mockups M3, M4, M5, M9, M10)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.services import infrastructure_operations as ops


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _accepted(admin_user, action="compute_teardown") -> int:
    op_id, _ = await ops.accept(action=action, principal=ops.Principal(admin_user.id))
    return op_id


@pytest.mark.asyncio
async def test_nothing_running(client, bench_token):
    resp = await client.get("/api/v1/infrastructure/operations/active", headers=_auth(bench_token))
    assert resp.status_code == 200
    assert resp.json() == {"busy": False, "state": None, "operation": None, "interrupted": []}


@pytest.mark.asyncio
async def test_users_without_infrastructure_actions_get_only_the_busy_signal(client, admin_user, bench_token):
    await _accepted(admin_user)
    body = (await client.get("/api/v1/infrastructure/operations/active", headers=_auth(bench_token))).json()
    assert (body["busy"], body["state"], body["operation"]) == (True, "running", None)


@pytest.mark.asyncio
async def test_comp_bio_gets_only_the_busy_signal(client, admin_user, comp_bio_token):
    await _accepted(admin_user)
    body = (await client.get("/api/v1/infrastructure/operations/active", headers=_auth(comp_bio_token))).json()
    assert body["busy"] is True and body["operation"] is None


@pytest.mark.asyncio
async def test_admins_get_the_running_operation(client, admin_user, admin_token):
    op_id = await _accepted(admin_user)
    body = (await client.get("/api/v1/infrastructure/operations/active", headers=_auth(admin_token))).json()
    assert body["operation"]["id"] == op_id
    assert body["operation"]["label"] == "Compute teardown"
    assert body["operation"]["status"] == "accepted"
    assert body["operation"]["steps"][0] == {
        "key": "accepted",
        "label": "Accepted",
        "state": "done",
        "detail": None,
        "completed": None,
        "total": None,
    }


@pytest.mark.asyncio
async def test_operation_detail_is_for_admins(client, admin_user, admin_token, comp_bio_token):
    op_id = await _accepted(admin_user)
    ok = await client.get(f"/api/v1/infrastructure/operations/{op_id}", headers=_auth(admin_token))
    refused = await client.get(f"/api/v1/infrastructure/operations/{op_id}", headers=_auth(comp_bio_token))
    assert ok.status_code == 200 and ok.json()["id"] == op_id
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_unseen_interrupted_outcomes_show_until_opened(client, admin_user, admin_token, session):
    op_id = await _accepted(admin_user)
    await session.execute(
        text("UPDATE infrastructure_operations SET status='running', runner=CAST(:r AS jsonb) WHERE id=:i").bindparams(
            r=json.dumps({"container_id": "c", "container_start": "b:1", "pid": 5}), i=op_id
        )
    )
    await session.commit()
    from unittest.mock import patch

    with patch(
        "app.services.infrastructure_operations.current_runner",
        return_value={"container_id": "c", "container_start": "b:2", "pid": 6},
    ):
        await ops.reconcile_on_startup(session)
    await session.commit()

    body = (await client.get("/api/v1/infrastructure/operations/active", headers=_auth(admin_token))).json()
    assert body["busy"] is False
    assert [o["id"] for o in body["interrupted"]] == [op_id]

    seen = await client.post(f"/api/v1/infrastructure/operations/{op_id}/seen", headers=_auth(admin_token))
    assert seen.status_code == 200
    body = (await client.get("/api/v1/infrastructure/operations/active", headers=_auth(admin_token))).json()
    assert body["interrupted"] == []


@pytest.mark.asyncio
async def test_recover_route_needs_configure_and_confirmation(client, admin_user, admin_token, comp_bio_token, session):
    op_id = await _accepted(admin_user)
    await session.execute(
        text(
            "UPDATE infrastructure_operations SET status='recovery_required', runner=CAST(:r AS jsonb) WHERE id=:i"
        ).bindparams(r=json.dumps({"container_id": "gone", "container_start": "b:1", "pid": 5}), i=op_id)
    )
    await session.commit()
    path = f"/api/v1/infrastructure/operations/{op_id}/recover"

    assert (
        await client.post(path, json={"reason": "x", "confirm": True}, headers=_auth(comp_bio_token))
    ).status_code == 403
    assert (
        await client.post(path, json={"reason": "x", "confirm": False}, headers=_auth(admin_token))
    ).status_code == 400
    ok = await client.post(path, json={"reason": "VM checked", "confirm": True}, headers=_auth(admin_token))
    assert ok.status_code == 200
    assert ok.json()["status"] == "interrupted"
    body = (await client.get("/api/v1/infrastructure/operations/active", headers=_auth(admin_token))).json()
    assert body["busy"] is False


@pytest.mark.asyncio
async def test_recent_operations_list(client, admin_user, admin_token):
    op_id = await _accepted(admin_user)
    body = (await client.get("/api/v1/infrastructure/operations", headers=_auth(admin_token))).json()
    assert [o["id"] for o in body["operations"]] == [op_id]


def test_startup_reconciles_operations():
    from app import startup

    names = [s.name for s in startup.NORMAL_STEPS]
    assert "infrastructure_operations" in names
