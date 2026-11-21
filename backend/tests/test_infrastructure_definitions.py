"""Infrastructure changes after a bioAF update (storage lifecycle 11I; approved M17).

The installed release's applicable definition fingerprints are compared with what was last fully
reconciled. A difference (or no record) is a pending notice; it is recorded without Terraform or
cloud calls, keeps its revision across application-only releases, and is replaced by a new revision
when the pending definitions change. Each admin's "Later" is theirs alone and never marks anything
applied. Only a full reconciliation advances a module's baseline.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from app.models.infrastructure_definition import InfrastructureDefinitionNotice
from app.services import infrastructure_definitions as definitions


@pytest.fixture
def modules(tmp_path, monkeypatch):
    """A release's module definitions on disk, per cloud."""
    roots = {"gcp": tmp_path / "gcp", "aws": tmp_path / "aws"}
    for root in roots.values():
        for module in ("storage", "compute", "image_build"):
            (root / module).mkdir(parents=True)
            (root / module / "main.tf").write_text(f'resource "x" "{module}" {{}}\n')
    monkeypatch.setattr(definitions, "modules_root", lambda cloud: roots[cloud])
    return roots


async def _config(session, **kv):
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()


async def _deployed(session):
    await _config(session, storage_deployed="true", compute_deployed="true", cloud_provider="gcp")


async def _reconcile_all(session):
    captured = await definitions.capture(session)
    for module in ("storage", "compute"):
        await definitions.record_reconciled(session, captured, module)
    await session.commit()


# ---------------------------------------------------------------------------
# fingerprints
# ---------------------------------------------------------------------------


def test_a_fingerprint_is_deterministic_and_ignores_local_artifacts(tmp_path):
    module = tmp_path / "storage"
    module.mkdir()
    (module / "main.tf").write_text("a")
    (module / "variables.tf").write_text("b")
    first = definitions.fingerprint_module(module)
    (module / ".terraform").mkdir()
    (module / ".terraform" / "provider").write_text("cache")
    (module / "terraform.tfstate").write_text("{}")
    (module / "terraform.tfvars.json").write_text("{}")
    (module / "tfplan").write_bytes(b"plan")
    assert definitions.fingerprint_module(module) == first
    (module / "main.tf").write_text("a changed")
    assert definitions.fingerprint_module(module) != first


def test_a_missing_module_has_no_fingerprint(tmp_path):
    assert definitions.fingerprint_module(tmp_path / "nope") is None


# ---------------------------------------------------------------------------
# the notice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nothing_deployed_means_nothing_pending(session, modules):
    assert await definitions.reconcile_notice(session, "2026.12.0") is None


@pytest.mark.asyncio
async def test_a_legacy_installation_reports_an_unknown_baseline(session, modules):
    await _deployed(session)
    notice = await definitions.reconcile_notice(session, "2026.12.0")
    assert notice.unknown_baseline is True
    assert notice.pending_modules == ["compute", "storage"]


@pytest.mark.asyncio
async def test_reconciled_definitions_mean_up_to_date(session, modules):
    await _deployed(session)
    await _reconcile_all(session)
    assert await definitions.reconcile_notice(session, "2026.12.0") is None


@pytest.mark.asyncio
async def test_a_changed_definition_is_pending_and_repeat_finalization_keeps_one_notice(session, modules):
    await _deployed(session)
    await _reconcile_all(session)
    (modules["gcp"] / "storage" / "main.tf").write_text('resource "x" "literature" {}\n')

    first = await definitions.reconcile_notice(session, "2026.12.0")
    again = await definitions.reconcile_notice(session, "2026.12.0")
    app_only_release = await definitions.reconcile_notice(session, "2026.12.1")
    await session.commit()

    assert (first.pending_modules, first.unknown_baseline) == (["storage"], False)
    assert again.id == first.id and app_only_release.id == first.id
    assert app_only_release.release_version == "2026.12.0"


@pytest.mark.asyncio
async def test_new_pending_definitions_are_a_new_notice(session, modules):
    await _deployed(session)
    await _reconcile_all(session)
    (modules["gcp"] / "storage" / "main.tf").write_text("v2")
    first = await definitions.reconcile_notice(session, "2026.12.0")
    (modules["gcp"] / "storage" / "main.tf").write_text("v3")
    second = await definitions.reconcile_notice(session, "2027.1.0")
    await session.commit()

    assert second.id != first.id
    rows = (
        (await session.execute(select(InfrastructureDefinitionNotice).order_by(InfrastructureDefinitionNotice.id)))
        .scalars()
        .all()
    )
    assert [r.resolved_at is not None for r in rows] == [True, False]


@pytest.mark.asyncio
async def test_another_clouds_changes_do_not_notify(session, modules):
    await _deployed(session)
    await _reconcile_all(session)
    (modules["aws"] / "storage" / "main.tf").write_text("aws-only change")
    assert await definitions.reconcile_notice(session, "2026.12.0") is None


@pytest.mark.asyncio
async def test_a_module_not_deployed_does_not_notify(session, modules):
    await _config(session, storage_deployed="true", compute_deployed="false")
    await _reconcile_all(session)
    (modules["gcp"] / "compute" / "main.tf").write_text("compute changed")
    assert await definitions.reconcile_notice(session, "2026.12.0") is None


@pytest.mark.asyncio
async def test_only_delete_or_replace_left_is_reconciled_but_reported(session, modules):
    await _deployed(session)
    captured = await definitions.capture(session)
    await definitions.record_reconciled(session, captured, "storage", destructive_pending=True)
    await definitions.record_reconciled(session, captured, "compute")
    await session.commit()

    assert await definitions.reconcile_notice(session, "2026.12.0") is None
    view = await definitions.notice_for(session, user_id=1)
    assert view["destructive_only_modules"] == ["storage"]


@pytest.mark.asyncio
async def test_reconciling_resolves_the_notice(session, modules):
    await _deployed(session)
    await definitions.reconcile_notice(session, "2026.12.0")
    await _reconcile_all(session)
    await definitions.reconcile_notice(session, "2026.12.0")
    await session.commit()
    assert await definitions.active_notice(session) is None


@pytest.mark.asyncio
async def test_a_baseline_records_what_ran_not_what_is_on_disk_later(session, modules):
    await _deployed(session)
    captured = await definitions.capture(session)  # before the operation ran
    (modules["gcp"] / "storage" / "main.tf").write_text("changed while it ran")
    await definitions.record_reconciled(session, captured, "storage")
    await definitions.record_reconciled(session, captured, "compute")
    await session.commit()
    notice = await definitions.reconcile_notice(session, "2026.12.0")
    assert notice is not None and notice.pending_modules == ["storage"]


@pytest.mark.asyncio
async def test_recording_the_notice_runs_no_terraform_and_touches_no_storage(session, modules):
    await _deployed(session)

    def forbidden(*_a, **_k):
        raise AssertionError("no Terraform or cloud call is allowed here")

    with (
        patch("app.services.terraform_executor.TerraformExecutor.run_plan", side_effect=forbidden),
        patch("app.services.terraform_executor.TerraformExecutor._run_captured", side_effect=forbidden),
        patch("app.adapters.registry.get_storage_adapter", side_effect=forbidden),
    ):
        assert await definitions.reconcile_notice(session, "2026.12.0") is not None


# ---------------------------------------------------------------------------
# per-admin acknowledgment
# ---------------------------------------------------------------------------


async def _two_admins(session):
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles
    from app.models.organization import Organization

    org = Organization(name="NoticeOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    roles = await seed_builtin_roles(session, org.id)
    users = []
    for email in ("a@test.com", "b@test.com"):
        user = User(
            email=email,
            password_hash=AuthService.hash_password("pw"),
            role_id=roles["admin"],
            organization_id=org.id,
            status="active",
        )
        session.add(user)
        users.append(user)
    await session.commit()
    return users[0].id, users[1].id


@pytest.mark.asyncio
async def test_one_admins_later_is_not_anothers_and_marks_nothing_applied(session, modules):
    a, b = await _two_admins(session)
    await _deployed(session)
    notice = await definitions.reconcile_notice(session, "2026.12.0")
    await session.commit()

    await definitions.acknowledge(session, notice.id, a)
    await definitions.acknowledge(session, notice.id, a)  # repeated: still one record
    await session.commit()

    view_a = await definitions.notice_for(session, a)
    view_b = await definitions.notice_for(session, b)
    assert (view_a["pending"], view_a["seen"]) == (True, True)
    assert (view_b["pending"], view_b["seen"]) == (True, False)
    assert (await definitions.active_notice(session)).id == notice.id


@pytest.mark.asyncio
async def test_a_new_notice_reappears_for_an_admin_who_dismissed_the_old_one(session, modules):
    a, _b = await _two_admins(session)
    await _deployed(session)
    await _reconcile_all(session)
    (modules["gcp"] / "storage" / "main.tf").write_text("v2")
    first = await definitions.reconcile_notice(session, "2026.12.0")
    await definitions.acknowledge(session, first.id, a)
    (modules["gcp"] / "storage" / "main.tf").write_text("v3")
    await definitions.reconcile_notice(session, "2027.1.0")
    await session.commit()
    view = await definitions.notice_for(session, a)
    assert (view["notice"]["version"], view["seen"]) == ("2027.1.0", False)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_notice_routes(client, admin_token, comp_bio_token, session, modules):
    await _deployed(session)
    notice = await definitions.reconcile_notice(session, "2026.12.0")
    await session.commit()

    body = (await client.get("/api/v1/infrastructure/definition-notice", headers=_auth(admin_token))).json()
    assert body["pending"] is True and body["notice"]["id"] == notice.id and body["seen"] is False
    path = f"/api/v1/infrastructure/definition-notice/{notice.id}/seen"
    assert (await client.post(path, headers=_auth(comp_bio_token))).status_code == 403
    seen = await client.post(path, headers=_auth(admin_token))
    assert seen.status_code == 200 and seen.json()["seen"] is True


# ---------------------------------------------------------------------------
# finalization (CLI) and what advances a baseline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_cli_refuses_a_version_that_is_not_running(session, modules):
    from app import database
    from app.cli.record_infrastructure_notice import run

    await _deployed(session)
    assert await run(["--to-version", "2027.1.0"], database.async_session_factory, "2026.12.0") == 2
    assert await definitions.active_notice(session) is None
    assert await run(["--to-version", "v2026.12.0"], database.async_session_factory, "2026.12.0") == 0
    session.expire_all()
    assert await definitions.active_notice(session) is not None


@pytest.mark.asyncio
async def test_a_full_update_check_reconciles_every_planned_module(session, admin_user, modules, monkeypatch):
    from app.services import infra_update_service
    from app.services import infrastructure_operations as ops

    await _deployed(session)
    await definitions.reconcile_notice(session, "2026.12.0")
    await session.commit()

    async def check(s, user_id):
        return {
            "has_changes": False,
            "has_additive": False,
            "has_destructive": True,
            "requires_approval": True,
            "realigned": None,
            "modules_with_additive": [],
            "modules": [
                {"module": "storage", "add_count": 0, "change_count": 0, "destroy_count": 1, "has_changes": True},
                {"module": "compute", "add_count": 0, "change_count": 0, "destroy_count": 0, "has_changes": False},
            ],
            "additive_resources": [],
            "destructive_resources": [
                {"address": "google_storage_bucket.raw", "type": "google_storage_bucket", "action": "replace"}
            ],
        }

    monkeypatch.setattr(infra_update_service, "check_for_updates", check)
    op_id, _ = await ops.accept(action="infrastructure_update", principal=ops.Principal(admin_user.id))
    await ops.run_operation(op_id, lambda h: infra_update_service.run_update_check(h, admin_user.id))

    user_id = admin_user.id
    session.expire_all()
    assert await definitions.active_notice(session) is None
    view = await definitions.notice_for(session, user_id)
    assert view["destructive_only_modules"] == ["storage"]


@pytest.mark.asyncio
async def test_a_failed_update_check_advances_nothing(session, admin_user, modules, monkeypatch):
    from app.exceptions import StateError
    from app.services import infra_update_service
    from app.services import infrastructure_operations as ops

    await _deployed(session)
    await definitions.reconcile_notice(session, "2026.12.0")
    await session.commit()

    async def check(s, user_id):
        raise StateError("Plan failed for storage")

    monkeypatch.setattr(infra_update_service, "check_for_updates", check)
    op_id, _ = await ops.accept(action="infrastructure_update", principal=ops.Principal(admin_user.id))
    await ops.run_operation(op_id, lambda h: infra_update_service.run_update_check(h, admin_user.id))

    session.expire_all()
    assert (await definitions.active_notice(session)) is not None


def test_the_repository_modules_are_fingerprinted():
    root = Path(definitions._REPO_TERRAFORM) / "modules"
    assert definitions.fingerprint_module(root / "storage") is not None
