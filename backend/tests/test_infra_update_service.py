"""Unit tests for the infrastructure-update check service.

The destructive classification is a pure function (fully covered here). The
orchestration is exercised with TerraformExecutor.run_plan monkeypatched to
return controlled plans, so no real Terraform/GCP is needed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

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


# ---------------------------------------------------------------------------
# classify_destructive (pure)
# ---------------------------------------------------------------------------


def test_classify_flags_only_stateful_destroy_or_replace():
    plan = _plan(
        [
            _resource("module.storage.google_storage_bucket.literature", "google_storage_bucket", "create"),
            _resource("module.storage.google_storage_bucket.raw", "google_storage_bucket", "delete"),
            _resource("module.storage.google_storage_bucket.results", "google_storage_bucket", "replace"),
            _resource("module.storage.google_storage_bucket_iam_member.x", "google_storage_bucket_iam_member", "delete"),
            _resource("module.compute.google_container_node_pool.pipeline", "google_container_node_pool", "replace"),
        ]
    )
    flagged = {r["address"] for r in infra_update_service.classify_destructive(plan)}
    assert flagged == {
        "module.storage.google_storage_bucket.raw",
        "module.storage.google_storage_bucket.results",
    }


def test_classify_empty_and_additive_only():
    assert infra_update_service.classify_destructive(None) == []
    assert infra_update_service.classify_destructive(_plan([])) == []
    additive = _plan(
        [
            _resource("a.google_storage_bucket.b", "google_storage_bucket", "create"),
            _resource("a.google_storage_bucket.c", "google_storage_bucket", "update"),
        ]
    )
    assert infra_update_service.classify_destructive(additive) == []


# ---------------------------------------------------------------------------
# check_for_updates (orchestration)
# ---------------------------------------------------------------------------


async def _seed(session, **kv):
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.flush()


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


@pytest.mark.asyncio
async def test_check_safe_changes_plan_storage_and_compute(session, admin_user, monkeypatch):
    await _seed(session, terraform_initialized="true", storage_deployed="true", compute_deployed="true")
    plans = {
        "storage": _plan(
            [_resource("module.storage.google_storage_bucket.literature", "google_storage_bucket", "create")]
        ),
        "compute": _plan([]),
    }
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan(plans))

    result = await infra_update_service.check_for_updates(session, admin_user.id)

    assert result["has_changes"] is True
    assert result["requires_approval"] is False
    assert result["modules_with_changes"] == ["storage"]
    assert result["destructive_resources"] == []
    # Both modules were planned.
    assert {m["module"] for m in result["modules"]} == {"storage", "compute"}
    # Plan runs are retired so a later plan's stale-run recovery cannot fail them.
    runs = (await session.execute(select(TerraformRun))).scalars().all()
    assert runs and all(r.status == "cancelled" for r in runs)


@pytest.mark.asyncio
async def test_check_destructive_requires_approval(session, admin_user, monkeypatch):
    await _seed(session, terraform_initialized="true", storage_deployed="true")
    plans = {
        "storage": _plan(
            [_resource("module.storage.google_storage_bucket.raw", "google_storage_bucket", "delete")]
        )
    }
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan(plans))

    result = await infra_update_service.check_for_updates(session, admin_user.id)

    assert result["has_changes"] is True
    assert result["requires_approval"] is True
    assert len(result["destructive_resources"]) == 1
    assert result["destructive_resources"][0]["type"] == "google_storage_bucket"


@pytest.mark.asyncio
async def test_check_no_changes(session, admin_user, monkeypatch):
    await _seed(session, terraform_initialized="true", storage_deployed="true")
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan({"storage": _plan([])}))

    result = await infra_update_service.check_for_updates(session, admin_user.id)

    assert result["has_changes"] is False
    assert result["requires_approval"] is False
    assert result["modules_with_changes"] == []


@pytest.mark.asyncio
async def test_check_requires_terraform_initialized(session, admin_user):
    with pytest.raises(ValueError):
        await infra_update_service.check_for_updates(session, admin_user.id)


@pytest.mark.asyncio
async def test_check_requires_something_deployed(session, admin_user):
    await _seed(session, terraform_initialized="true")
    with pytest.raises(ValueError):
        await infra_update_service.check_for_updates(session, admin_user.id)
