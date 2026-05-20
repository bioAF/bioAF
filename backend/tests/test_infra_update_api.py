"""API tests for the infrastructure-update check / apply endpoints.

TerraformExecutor.run_plan is monkeypatched to return controlled plans and
the background apply launcher is replaced with a recorder, so no real
Terraform runs. raw_bucket_name is seeded so re-align parses it without
touching live state.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.component import TerraformRun
from app.services import infra_update_service
from app.services.terraform_executor import TerraformExecutor


def _resource(address: str, rtype: str, action: str) -> dict:
    return {
        "address": address,
        "type": rtype,
        "name": address.split(".")[-1],
        "action": action,
        "description": f"{rtype}: {address}",
    }


def _plan(resources: list[dict]) -> dict:
    add = sum(1 for r in resources if r["action"] in ("create", "replace"))
    chg = sum(1 for r in resources if r["action"] == "update")
    dst = sum(1 for r in resources if r["action"] in ("delete", "replace"))
    return {
        "add_count": add,
        "change_count": chg,
        "destroy_count": dst,
        "total": add + chg + dst,
        "resources": resources,
    }


async def _seed(session, **kv):
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()


def _fake_run_plan(plans: dict[str, dict]):
    async def run_plan(session, user_id, module_name):
        plan = plans.get(module_name, _plan([]))
        run = TerraformRun(
            triggered_by_user_id=user_id,
            action="plan",
            module_name=module_name,
            status="awaiting_confirmation",
            plan_json=plan,
            resources_planned=plan["total"],
        )
        session.add(run)
        await session.flush()
        return run

    return run_plan


async def _noop_persist(*_args, **_kwargs):
    return None


CHECK = "/api/v1/infrastructure/stack/check-updates"
APPLY = "/api/v1/infrastructure/stack/apply-updates"
LIT = _resource("module.storage.google_storage_bucket.literature", "google_storage_bucket", "create")
RAW_DELETE = _resource("module.storage.google_storage_bucket.raw", "google_storage_bucket", "delete")


@pytest.mark.asyncio
async def test_check_updates_requires_permission(client, viewer_token):
    r = await client.post(CHECK, headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_check_updates_additive_auto_applies(client, admin_token, session, monkeypatch):
    await _seed(
        session,
        terraform_initialized="true",
        storage_deployed="true",
        compute_deployed="true",
        raw_bucket_name="bioaf-raw-bioaf-co-41aae5",
    )
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", _noop_persist)
    monkeypatch.setattr(
        TerraformExecutor, "run_plan", _fake_run_plan({"storage": _plan([LIT]), "compute": _plan([])})
    )
    launched: dict = {}
    monkeypatch.setattr(
        infra_update_service,
        "launch_background_apply",
        lambda modules, user_id: launched.update(modules=modules),
    )

    r = await client.post(CHECK, headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_additive"] is True
    assert body["has_destructive"] is False
    assert body["applying"] is True
    assert body["modules_with_additive"] == ["storage"]
    assert any("literature" in r["address"] for r in body["additive_resources"])
    assert launched["modules"] == ["storage"]


@pytest.mark.asyncio
async def test_check_updates_destructive_not_auto_applied(client, admin_token, session, monkeypatch):
    await _seed(
        session,
        terraform_initialized="true",
        storage_deployed="true",
        org_slug="bioaf-co",
        deploy_suffix="41aae5",
        raw_bucket_name="bioaf-raw-bioaf-co-41aae5",
    )
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", _noop_persist)
    monkeypatch.setattr(
        TerraformExecutor, "run_plan", _fake_run_plan({"storage": _plan([LIT, RAW_DELETE])})
    )
    calls = {"n": 0}
    monkeypatch.setattr(
        infra_update_service,
        "launch_background_apply",
        lambda modules, user_id: calls.__setitem__("n", calls["n"] + 1),
    )

    r = await client.post(CHECK, headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_additive"] is True
    assert body["has_destructive"] is True
    assert body["requires_approval"] is True
    assert body["applying"] is False
    # Literature create is visible alongside the flagged destructive bucket.
    assert any("literature" in r["address"] for r in body["additive_resources"])
    dest = {r["address"]: r for r in body["destructive_resources"]}
    assert dest["module.storage.google_storage_bucket.raw"]["stateful"] is True
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_check_updates_nothing_deployed_returns_400(client, admin_token, session):
    await _seed(session, terraform_initialized="true")
    r = await client.post(CHECK, headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_apply_updates_launches_valid_modules(client, admin_token, monkeypatch):
    launched: dict = {}
    monkeypatch.setattr(
        infra_update_service,
        "launch_background_apply",
        lambda modules, user_id: launched.update(modules=modules),
    )
    r = await client.post(
        APPLY, json={"modules": ["storage", "bogus"]}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["modules"] == ["storage"]
    assert launched["modules"] == ["storage"]


@pytest.mark.asyncio
async def test_apply_updates_rejects_no_valid_modules(client, admin_token):
    r = await client.post(
        APPLY, json={"modules": ["bogus"]}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 400
