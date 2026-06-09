"""Unit tests for the infrastructure-update service.

Pure plan helpers and name parsing are covered directly. Orchestration is
exercised with TerraformExecutor.run_plan monkeypatched to return controlled
plans, so no real Terraform/GCP is needed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.exceptions import StateError
from app.models.component import TerraformRun
from app.services import infra_update_service
from app.platform.platform_config_service import PlatformConfigService
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
# Pure plan helpers
# ---------------------------------------------------------------------------


def test_classify_flags_only_stateful_destroy_or_replace():
    plan = _plan(
        [
            _resource("m.google_storage_bucket.lit", "google_storage_bucket", "create"),
            _resource("m.google_storage_bucket.raw", "google_storage_bucket", "delete"),
            _resource("m.google_storage_bucket.res", "google_storage_bucket", "replace"),
            _resource("m.google_storage_bucket_iam_member.x", "google_storage_bucket_iam_member", "delete"),
            _resource("m.google_container_node_pool.p", "google_container_node_pool", "replace"),
        ]
    )
    flagged = {r["address"] for r in infra_update_service.classify_destructive(plan)}
    assert flagged == {"m.google_storage_bucket.raw", "m.google_storage_bucket.res"}


def test_list_destructive_marks_stateful_flag():
    plan = _plan(
        [
            _resource("m.google_storage_bucket.raw", "google_storage_bucket", "delete"),
            _resource("m.google_container_node_pool.p", "google_container_node_pool", "replace"),
        ]
    )
    by_addr = {r["address"]: r for r in infra_update_service.list_destructive(plan)}
    assert by_addr["m.google_storage_bucket.raw"]["stateful"] is True
    assert by_addr["m.google_container_node_pool.p"]["stateful"] is False


def test_additive_resources_excludes_destructive_and_data_sources():
    plan = _plan(
        [
            _resource("m.google_storage_bucket.lit", "google_storage_bucket", "create"),
            _resource("m.google_storage_bucket.raw", "google_storage_bucket", "update"),
            _resource("m.google_storage_bucket.old", "google_storage_bucket", "delete"),
            _resource("data.google_project.current", "google_project", "create"),
        ]
    )
    addrs = set(infra_update_service.additive_addresses(plan))
    assert addrs == {"m.google_storage_bucket.lit", "m.google_storage_bucket.raw"}


def test_parse_bucket_name():
    assert infra_update_service._parse_bucket_name("bioaf-raw-bioaf-co-41aae5", "raw") == ("bioaf-co", "41aae5")
    # Old-style name without a six-hex suffix is rejected (caller falls back).
    assert infra_update_service._parse_bucket_name("bioaf-raw-bioaf-co", "raw") is None
    assert infra_update_service._parse_bucket_name("something-else", "raw") is None


# ---------------------------------------------------------------------------
# Re-align
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


@pytest.mark.asyncio
async def test_realign_sets_storage_suffix_not_shared_deploy_suffix(session):
    # Storage buckets at -4bd459 while the shared deploy_suffix is the compute
    # value -41aae5. Re-align must pin storage_stack_uid and leave deploy_suffix
    # alone, so compute plans are not pushed into a cluster replace.
    await _seed(
        session,
        raw_bucket_name="bioaf-raw-bioaf-co-4bd459",
        deploy_suffix="41aae5",
    )
    changed = await infra_update_service.realign_storage_naming(session)
    assert changed == {"org_slug": "bioaf-co", "stack_uid": "4bd459"}
    assert await PlatformConfigService.get(session, "storage_stack_uid") == "4bd459"
    # The shared deploy_suffix (compute's) is untouched.
    assert await PlatformConfigService.get(session, "deploy_suffix") == "41aae5"


@pytest.mark.asyncio
async def test_realign_noop_when_already_aligned(session):
    await _seed(
        session,
        raw_bucket_name="bioaf-raw-bioaf-co-41aae5",
        org_slug="bioaf-co",
        storage_stack_uid="41aae5",
    )
    assert await infra_update_service.realign_storage_naming(session) is None


# ---------------------------------------------------------------------------
# check_for_updates orchestration
# ---------------------------------------------------------------------------


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


class _PersistSpy:
    """Records the modules whose outputs were persisted after an apply."""

    def __init__(self) -> None:
        self.modules: list[str] = []

    async def __call__(self, _session, module: str) -> None:
        self.modules.append(module)


def _fake_run_apply(outcomes: dict[str, tuple[str, str | None]]):
    """Fake TerraformExecutor.run_apply: set the run's final status/error from
    the single target, mirroring how the real apply records its result."""

    async def run_apply(session, run_id, user_id, targets=None):
        run = (await session.execute(select(TerraformRun).where(TerraformRun.id == run_id))).scalar_one()
        addr = (targets or [None])[0]
        status, err = outcomes.get(addr, ("completed", None))
        run.status = status
        run.error_message = err
        await session.flush()
        if False:  # pragma: no cover - makes this an async generator
            yield

    return run_apply


@pytest.mark.asyncio
async def test_check_realigns_then_reports_additive_literature(session, admin_user, monkeypatch):
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", _noop_persist)
    await _seed(
        session,
        terraform_initialized="true",
        storage_deployed="true",
        compute_deployed="true",
        raw_bucket_name="bioaf-raw-bioaf-co-41aae5",
    )
    plans = {
        "storage": _plan(
            [_resource("module.storage.google_storage_bucket.literature", "google_storage_bucket", "create")]
        ),
        "compute": _plan([]),
    }
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan(plans))

    result = await infra_update_service.check_for_updates(session, admin_user.id)

    assert result["realigned"] == {"org_slug": "bioaf-co", "stack_uid": "41aae5"}
    assert result["has_additive"] is True
    assert result["has_destructive"] is False
    assert result["requires_approval"] is False
    assert result["modules_with_additive"] == ["storage"]
    assert any("literature" in r["address"] for r in result["additive_resources"])
    # Plan runs are retired.
    runs = (await session.execute(select(TerraformRun))).scalars().all()
    assert runs and all(r.status == "cancelled" for r in runs)


@pytest.mark.asyncio
async def test_check_reports_destructive_and_requires_approval(session, admin_user, monkeypatch):
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", _noop_persist)
    await _seed(
        session,
        terraform_initialized="true",
        storage_deployed="true",
        org_slug="bioaf-co",
        deploy_suffix="41aae5",
        raw_bucket_name="bioaf-raw-bioaf-co-41aae5",
    )
    plans = {
        "storage": _plan(
            [
                _resource("module.storage.google_storage_bucket.literature", "google_storage_bucket", "create"),
                _resource("module.storage.google_storage_bucket.raw", "google_storage_bucket", "replace"),
            ]
        )
    }
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan(plans))

    result = await infra_update_service.check_for_updates(session, admin_user.id)

    assert result["has_additive"] is True
    assert result["has_destructive"] is True
    assert result["requires_approval"] is True
    dest = {r["address"]: r for r in result["destructive_resources"]}
    assert dest["module.storage.google_storage_bucket.raw"]["stateful"] is True


@pytest.mark.asyncio
async def test_check_self_heals_storage_bucket_names(session, admin_user, monkeypatch):
    """A bucket that exists in state but was never recorded (e.g. literature)
    gets persisted to platform_config during the check, so uploads work and the
    Components view lists every bucket."""
    await _seed(
        session,
        terraform_initialized="true",
        storage_deployed="true",
        raw_bucket_name="bioaf-raw-bioaf-co-4bd459",
    )
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan({"storage": _plan([])}))

    async def fake_outputs(_session, _module):
        return {
            "literature_bucket_name": {"value": "bioaf-literature-bioaf-co-4bd459"},
            "references_bucket_name": {"value": "bioaf-references-bioaf-co-4bd459"},
            "raw_bucket_name": {"value": "bioaf-raw-bioaf-co-4bd459"},
        }

    monkeypatch.setattr(TerraformExecutor, "read_module_outputs", fake_outputs)

    await infra_update_service.check_for_updates(session, admin_user.id)

    assert await PlatformConfigService.get(session, "literature_bucket_name") == "bioaf-literature-bioaf-co-4bd459"
    assert await PlatformConfigService.get(session, "references_bucket_name") == "bioaf-references-bioaf-co-4bd459"


# ---------------------------------------------------------------------------
# apply_modules_sequentially: per-target apply + benign no-op handling
# ---------------------------------------------------------------------------


def test_is_benign_apply_error():
    assert infra_update_service._is_benign_apply_error(
        "googleapi: Error 400: Must specify a field to update., badRequest"
    )
    assert not infra_update_service._is_benign_apply_error("Error: quota exceeded")
    assert not infra_update_service._is_benign_apply_error(None)


@pytest.mark.asyncio
async def test_apply_skips_benign_noop_and_lands_real_create(session, admin_user, monkeypatch):
    """A no-op update GCP rejects with 'Must specify a field to update' must not
    abort the batch: the real new resource still applies and is persisted."""
    spy = _PersistSpy()
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", spy)
    plan = _plan(
        [
            _resource("module.compute.google_storage_bucket.lit", "google_storage_bucket", "create"),
            _resource("module.compute.google_container_node_pool.pipelines", "google_container_node_pool", "update"),
        ]
    )
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan({"compute": plan}))
    monkeypatch.setattr(
        TerraformExecutor,
        "run_apply",
        _fake_run_apply(
            {
                "module.compute.google_storage_bucket.lit": ("completed", None),
                "module.compute.google_container_node_pool.pipelines": (
                    "failed",
                    "googleapi: Error 400: Must specify a field to update., badRequest",
                ),
            }
        ),
    )

    await infra_update_service.apply_modules_sequentially(["compute"], admin_user.id)

    # The real create landed, so outputs were persisted exactly once.
    assert spy.modules == ["compute"]
    # The benign-failed run was reconciled to completed, not left failed.
    failed = (await session.execute(select(TerraformRun).where(TerraformRun.status == "failed"))).scalars().all()
    assert failed == []


@pytest.mark.asyncio
async def test_apply_does_not_swallow_real_failure(session, admin_user, monkeypatch):
    """A genuine apply failure stays failed and does not persist outputs."""
    spy = _PersistSpy()
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", spy)
    plan = _plan(
        [_resource("module.compute.google_container_node_pool.pipelines", "google_container_node_pool", "update")]
    )
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan({"compute": plan}))
    monkeypatch.setattr(
        TerraformExecutor,
        "run_apply",
        _fake_run_apply({"module.compute.google_container_node_pool.pipelines": ("failed", "Error: quota exceeded")}),
    )

    await infra_update_service.apply_modules_sequentially(["compute"], admin_user.id)

    assert spy.modules == []
    failed = (await session.execute(select(TerraformRun).where(TerraformRun.status == "failed"))).scalars().all()
    assert len(failed) == 1
    assert "quota" in (failed[0].error_message or "")


@pytest.mark.asyncio
async def test_apply_noop_when_no_additive_targets(session, admin_user, monkeypatch):
    """A module whose plan has no additive resources applies nothing."""
    spy = _PersistSpy()
    monkeypatch.setattr(infra_update_service, "_persist_module_outputs", spy)
    monkeypatch.setattr(TerraformExecutor, "run_plan", _fake_run_plan({"compute": _plan([])}))
    monkeypatch.setattr(TerraformExecutor, "run_apply", _fake_run_apply({}))

    await infra_update_service.apply_modules_sequentially(["compute"], admin_user.id)

    assert spy.modules == []


@pytest.mark.asyncio
async def test_check_requires_terraform_initialized(session, admin_user):
    with pytest.raises(StateError):
        await infra_update_service.check_for_updates(session, admin_user.id)


@pytest.mark.asyncio
async def test_check_requires_something_deployed(session, admin_user):
    await _seed(session, terraform_initialized="true")
    with pytest.raises(StateError):
        await infra_update_service.check_for_updates(session, admin_user.id)
