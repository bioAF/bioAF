"""An infrastructure update batch stops at a real failure (storage lifecycle 11D, issue #3).

A failed target ends the batch: no later target or module starts, and the outcome says what was
applied first. A classified benign no-op does not stop it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.component import TerraformRun
from app.services import infra_update_service
from app.services.terraform_executor import TerraformExecutor
from tests.test_infra_update_service import _PersistSpy, _fake_run_apply, _fake_run_plan, _plan, _resource

FIRST = _resource("module.compute.google_container_node_pool.a", "google_container_node_pool", "create")
SECOND = _resource("module.compute.google_container_node_pool.b", "google_container_node_pool", "create")
STORAGE = _resource("module.storage.google_storage_bucket.literature", "google_storage_bucket", "create")


@pytest.mark.asyncio
async def test_a_failed_target_stops_later_targets_and_modules(session, admin_user, monkeypatch):
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", _PersistSpy())
    planned: list[str] = []
    fake_plan = _fake_run_plan({"compute": _plan([FIRST, SECOND]), "storage": _plan([STORAGE])})

    async def run_plan(s, user_id, module_name, **kwargs):
        planned.append(module_name)
        return await fake_plan(s, user_id, module_name, **kwargs)

    monkeypatch.setattr(TerraformExecutor, "run_plan", run_plan)
    monkeypatch.setattr(TerraformExecutor, "run_apply", _fake_run_apply({FIRST["address"]: ("failed", "quota")}))

    result = await infra_update_service.apply_modules_sequentially(["compute", "storage"], admin_user.id)

    assert result.status == "failed"
    assert result.partial_effects == {"added": 0, "failed_target": FIRST["address"]}
    # compute planned once to enumerate and once for the failed target; SECOND and storage never start.
    assert planned == ["compute", "compute"]
    applied = (
        (await session.execute(select(TerraformRun).where(TerraformRun.status.in_(["completed", "failed"]))))
        .scalars()
        .all()
    )
    assert [r.status for r in applied] == ["failed"]


@pytest.mark.asyncio
async def test_a_benign_noop_does_not_stop_the_batch(session, admin_user, monkeypatch):
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", _PersistSpy())
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan({"compute": _plan([FIRST, SECOND])}))
    monkeypatch.setattr(
        TerraformExecutor,
        "run_apply",
        _fake_run_apply({FIRST["address"]: ("failed", "googleapi: Error 400: Must specify a field to update")}),
    )

    result = await infra_update_service.apply_modules_sequentially(["compute"], admin_user.id)

    assert result.status == "succeeded"
    assert result.message == "Updates applied: 1 resource added."


@pytest.mark.asyncio
async def test_nothing_to_apply_reports_up_to_date(session, admin_user, monkeypatch):
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan({"compute": _plan([])}))
    result = await infra_update_service.apply_modules_sequentially(["compute"], admin_user.id)
    assert (result.status, result.message) == ("succeeded", "Your infrastructure is up to date.")


@pytest.mark.asyncio
async def test_a_failed_plan_stops_the_batch(session, admin_user, monkeypatch):
    async def failing_plan(s, user_id, module_name, **_kwargs):
        run = TerraformRun(triggered_by_user_id=user_id, action="plan", module_name=module_name, status="failed")
        s.add(run)
        await s.flush()
        return run

    monkeypatch.setattr(TerraformExecutor, "run_plan", failing_plan)
    result = await infra_update_service.apply_modules_sequentially(["compute", "storage"], admin_user.id)
    assert result.status == "failed" and result.category == "plan_failed"


@pytest.mark.asyncio
async def test_a_target_whose_plan_would_replace_something_is_not_applied(session, admin_user, monkeypatch):
    """-target also applies dependencies; a dependency marked for replacement stops the batch."""
    from app.models.component import TerraformRun

    full = _plan([FIRST])
    targeted = _plan(
        [FIRST, _resource("module.compute.google_container_cluster.main", "google_container_cluster", "replace")]
    )

    async def run_plan(s, user_id, module_name, targets=None):
        plan = targeted if targets else full
        run = TerraformRun(
            triggered_by_user_id=user_id,
            action="plan",
            module_name=module_name,
            status="awaiting_confirmation",
            plan_json=plan,
            resources_planned=plan["total"],
        )
        s.add(run)
        await s.flush()
        return run

    applied: list = []

    async def run_apply(s, run_id, user_id, targets=None):
        applied.append(targets)
        if False:  # pragma: no cover
            yield

    monkeypatch.setattr(TerraformExecutor, "run_plan", run_plan)
    monkeypatch.setattr(TerraformExecutor, "run_apply", run_apply)

    result = await infra_update_service.apply_modules_sequentially(["compute"], admin_user.id)

    assert applied == []
    assert result.status == "failed" and result.category == "scope_exceeded"
    assert result.partial_effects["out_of_scope"] == ["module.compute.google_container_cluster.main"]


@pytest.mark.asyncio
async def test_a_target_whose_plan_adds_something_unconfirmed_is_not_applied(session, admin_user, monkeypatch):
    from app.models.component import TerraformRun

    extra = _resource("module.compute.google_compute_firewall.open", "google_compute_firewall", "create")

    async def run_plan(s, user_id, module_name, targets=None):
        plan = _plan([FIRST, extra]) if targets else _plan([FIRST])
        run = TerraformRun(
            triggered_by_user_id=user_id,
            action="plan",
            module_name=module_name,
            status="awaiting_confirmation",
            plan_json=plan,
            resources_planned=plan["total"],
        )
        s.add(run)
        await s.flush()
        return run

    monkeypatch.setattr(TerraformExecutor, "run_plan", run_plan)
    result = await infra_update_service.apply_modules_sequentially(["compute"], admin_user.id)
    assert result.category == "scope_exceeded"
