"""Storage identifiers and generation retirement (storage lifecycle stage 11A).

A storage deployment is named by its storage generation (``storage_stack_uid``).
These tests pin the lifecycle of that identity:

- unset, empty and literal ``"null"`` values never become Terraform identifiers;
- a confirmed storage destroy retires the generation, a failed or interrupted
  one keeps it;
- a new storage deployment persists a fresh generation before Terraform runs,
  and retries reuse it;
- an earlier partial attempt whose identity cannot be established is refused
  before Terraform, instead of guessing a new name over existing resources.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.exceptions import StateError
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.terraform_cloud import AwsTerraformCloud, GcpTerraformCloud

BLANK_IDENTIFIERS = [None, "", "null", "NULL", " null ", "  "]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _seed_user(session) -> int:
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="StorageGenOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="storage_gen@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user.id


async def _set_config(session, key: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(k=key, v=value)
    )
    await session.flush()


async def _get_config(session, key: str) -> str | None:
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = :k").bindparams(k=key))).fetchone()
    return row[0] if row else None


async def _record_run(session, user_id: int, *, action: str, status: str, tfvars: dict | None = None) -> None:
    from app.models.component import TerraformRun

    session.add(
        TerraformRun(
            triggered_by_user_id=user_id,
            action=action,
            module_name="storage",
            status=status,
            tfvars_json=tfvars,
        )
    )
    await session.flush()
    await session.commit()


async def _ready_for_deploy(session) -> None:
    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "false")
    await session.commit()


def _event(event_type: str, message: str = "", **kwargs):
    from app.services.terraform_executor import TerraformProgressEvent

    return TerraformProgressEvent(event_type=event_type, message=message, **kwargs)


@pytest.fixture(autouse=True)
def _verified_buckets(monkeypatch):
    """Report every bucket as this installation's: storage destroy now checks identity before
    emptying anything (storage lifecycle 11G)."""
    from app.services import storage_identity

    async def inspect(session, store, bucket_name):
        return storage_identity.StoreReport(store, bucket_name, storage_identity.StoreHealth.AVAILABLE, "discovered")

    monkeypatch.setattr(storage_identity, "inspect_bucket", inspect)


@pytest.fixture(autouse=True)
def _stub_capacity_probe(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.services.stack_deployment._select_default_pool_zone",
        AsyncMock(return_value="us-central1-a"),
    )


def _storage_only_run_module(seen: list[str | None], storage_event: str = "apply_complete"):
    """A fake module runner that records the storage generation visible to Terraform."""

    async def run_module(sess, uid, module_name):
        if module_name == "storage":
            seen.append(await _get_config(sess, "storage_stack_uid"))
            yield _event(storage_event, "storage")
            return
        yield _event(
            "apply_complete",
            "compute",
            extra={
                "outputs": {
                    "cluster_name": {"value": "c"},
                    "cluster_endpoint": {"value": "https://1.2.3.4"},
                    "cluster_ca_cert": {"value": "Y2VydA=="},
                }
            },
        )

    return run_module


# ---------------------------------------------------------------------------
# identifiers in tfvars
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cloud_cls", [GcpTerraformCloud, AwsTerraformCloud])
@pytest.mark.parametrize("blank", BLANK_IDENTIFIERS)
def test_blank_storage_identifiers_never_reach_tfvars(tmp_path, cloud_cls, blank):
    config = {"gcp_project_id": "p", "aws_region": "us-east-1", "org_slug": "acme"}
    if blank is not None:
        config["storage_stack_uid"] = blank
        config["deploy_suffix"] = blank
    tf = cloud_cls().write_tfvars(tmp_path, "storage", config)
    assert "stack_uid" not in tf


@pytest.mark.parametrize("cloud_cls", [GcpTerraformCloud, AwsTerraformCloud])
def test_null_storage_uid_falls_back_to_a_real_deploy_suffix(tmp_path, cloud_cls):
    config = {"gcp_project_id": "p", "org_slug": "acme", "storage_stack_uid": "null", "deploy_suffix": "a1b2c3"}
    tf = cloud_cls().write_tfvars(tmp_path, "storage", config)
    assert tf["stack_uid"] == "a1b2c3"


@pytest.mark.parametrize("cloud_cls", [GcpTerraformCloud, AwsTerraformCloud])
@pytest.mark.parametrize("blank", BLANK_IDENTIFIERS)
def test_blank_compute_identifiers_never_reach_tfvars(tmp_path, cloud_cls, blank):
    config = {"gcp_project_id": "p", "aws_region": "us-east-1", "org_slug": "acme"}
    if blank is not None:
        config["compute_stack_uid"] = blank
        config["deploy_suffix"] = blank
    tf = cloud_cls().write_tfvars(tmp_path, "compute", config)
    assert "stack_uid" not in tf


@pytest.mark.parametrize("cloud_cls", [GcpTerraformCloud, AwsTerraformCloud])
def test_null_org_slug_uses_the_default_slug(tmp_path, cloud_cls):
    tf = cloud_cls().write_tfvars(tmp_path, "storage", {"gcp_project_id": "p", "org_slug": "null"})
    assert tf["org_slug"] == "bioaf"


# ---------------------------------------------------------------------------
# retirement on destroy
# ---------------------------------------------------------------------------


async def _destroy(session, user_id: int, destroy_events: list[str]) -> list:
    from app.services.stack_deployment import destroy_storage

    async def run_destroy(sess, uid, module_name):
        for event_type in destroy_events:
            yield _event(event_type, "destroy")

    async def empty(sess, bucket_name):
        return 0

    events = []
    with (
        patch("app.services.stack_deployment._empty_gcs_bucket", side_effect=empty),
        patch("app.services.stack_deployment._run_destroy", side_effect=run_destroy),
    ):
        async for event in destroy_storage(session, user_id=user_id):
            events.append(event)
    await session.commit()
    return events


async def _deployed_storage(session) -> None:
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "storage_deployed", "true")
    await _set_config(session, "storage_stack_uid", "abc123")
    await _set_config(session, "raw_bucket_name", "bioaf-raw-acme-abc123")
    await _set_config(session, "compute_stack_uid", "c0ffee")
    await _set_config(session, "terraform_state_bucket", "bioaf-tfstate-p")
    await session.commit()


@pytest.mark.asyncio
async def test_confirmed_storage_destroy_retires_the_storage_generation(session):
    user_id = await _seed_user(session)
    await _deployed_storage(session)

    events = await _destroy(session, user_id, ["apply_complete"])

    assert events[-1].event_type == "stack_complete"
    assert await _get_config(session, "storage_stack_uid") is None
    # Compute identity and Terraform state storage are not storage generations.
    assert await _get_config(session, "compute_stack_uid") == "c0ffee"
    assert await _get_config(session, "terraform_state_bucket") == "bioaf-tfstate-p"


@pytest.mark.asyncio
async def test_failed_storage_destroy_keeps_the_storage_generation(session):
    user_id = await _seed_user(session)
    await _deployed_storage(session)

    events = await _destroy(session, user_id, ["apply_error"])

    assert events[-1].event_type == "stack_error"
    assert await _get_config(session, "storage_stack_uid") == "abc123"


@pytest.mark.asyncio
async def test_interrupted_storage_destroy_keeps_the_storage_generation(session):
    from app.services.terraform_stream import INTERRUPTED_EVENT

    user_id = await _seed_user(session)
    await _deployed_storage(session)

    await _destroy(session, user_id, [INTERRUPTED_EVENT])

    assert await _get_config(session, "storage_stack_uid") == "abc123"


# ---------------------------------------------------------------------------
# allocation on deploy
# ---------------------------------------------------------------------------


async def _deploy(session, user_id: int, run_module) -> list:
    from app.services.stack_deployment import deploy_stack

    events = []
    with patch("app.services.stack_deployment._run_module", side_effect=run_module):
        async for event in deploy_stack(session, "kubernetes", user_id=user_id):
            events.append(event)
    await session.commit()
    return events


@pytest.mark.asyncio
async def test_fresh_storage_deploy_persists_a_generation_before_terraform(session):
    user_id = await _seed_user(session)
    await _ready_for_deploy(session)

    seen: list[str | None] = []
    await _deploy(session, user_id, _storage_only_run_module(seen))

    assert len(seen) == 1
    assert seen[0] is not None and len(seen[0]) == 6
    int(seen[0], 16)
    assert await _get_config(session, "storage_stack_uid") == seen[0]


@pytest.mark.asyncio
async def test_literal_null_generation_is_replaced_on_a_fresh_deploy(session):
    user_id = await _seed_user(session)
    await _ready_for_deploy(session)
    await _set_config(session, "storage_stack_uid", "null")
    await session.commit()

    seen: list[str | None] = []
    await _deploy(session, user_id, _storage_only_run_module(seen))

    assert seen[0] not in (None, "", "null")
    assert len(seen[0]) == 6


@pytest.mark.asyncio
async def test_retry_after_failed_storage_deploy_reuses_the_generation(session):
    user_id = await _seed_user(session)
    await _ready_for_deploy(session)

    seen: list[str | None] = []
    first = await _deploy(session, user_id, _storage_only_run_module(seen, storage_event="apply_error"))
    assert first[-1].event_type == "stack_error"
    # The failed attempt reached apply: record it the way the executor would.
    await _record_run(session, user_id, action="apply", status="failed", tfvars={"stack_uid": seen[0]})

    await _deploy(session, user_id, _storage_only_run_module(seen))

    assert len(seen) == 2
    assert seen[0] == seen[1]


@pytest.mark.asyncio
async def test_deploy_after_confirmed_destroy_uses_a_new_generation(session):
    user_id = await _seed_user(session)
    await _deployed_storage(session)
    await _record_run(session, user_id, action="apply", status="completed", tfvars={"stack_uid": "abc123"})
    await _destroy(session, user_id, ["apply_complete"])
    await _record_run(session, user_id, action="destroy", status="completed")
    await _set_config(session, "gcp_credentials_configured", "true")
    await _set_config(session, "terraform_initialized", "true")
    await session.commit()

    seen: list[str | None] = []
    await _deploy(session, user_id, _storage_only_run_module(seen))

    assert seen[0] not in (None, "abc123")
    assert len(seen[0]) == 6


@pytest.mark.asyncio
async def test_legacy_partial_attempt_reuses_its_recorded_generation(session):
    """A pre-fix failed attempt kept no storage_stack_uid, but its run recorded the name it used."""
    user_id = await _seed_user(session)
    await _ready_for_deploy(session)
    await _record_run(session, user_id, action="apply", status="failed", tfvars={"stack_uid": "beef01"})

    seen: list[str | None] = []
    await _deploy(session, user_id, _storage_only_run_module(seen))

    assert seen == ["beef01"]


@pytest.mark.asyncio
async def test_legacy_partial_attempt_without_identity_is_refused_before_terraform(session):
    user_id = await _seed_user(session)
    await _ready_for_deploy(session)
    await _record_run(session, user_id, action="apply", status="failed", tfvars={"org_slug": "acme"})

    seen: list[str | None] = []
    with pytest.raises(StateError, match="earlier storage deployment"):
        await _deploy(session, user_id, _storage_only_run_module(seen))

    assert seen == []
    assert await _get_config(session, "storage_stack_uid") is None


@pytest.mark.asyncio
async def test_recorded_bucket_names_without_identity_are_refused_before_terraform(session):
    user_id = await _seed_user(session)
    await _ready_for_deploy(session)
    await _set_config(session, "raw_bucket_name", "bioaf-raw-acme-0a0b0c")
    await session.commit()

    seen: list[str | None] = []
    with pytest.raises(StateError, match="earlier storage deployment"):
        await _deploy(session, user_id, _storage_only_run_module(seen))

    assert seen == []


@pytest.mark.asyncio
async def test_plan_that_never_applied_is_not_evidence_of_resources(session):
    user_id = await _seed_user(session)
    await _ready_for_deploy(session)
    await _record_run(session, user_id, action="plan", status="failed", tfvars={"org_slug": "acme"})

    seen: list[str | None] = []
    await _deploy(session, user_id, _storage_only_run_module(seen))

    assert len(seen) == 1 and seen[0] is not None


@pytest.mark.asyncio
async def test_direct_storage_deploy_persists_a_generation_before_terraform(session):
    from app.api.storage_deploy import deploy_storage_module

    user_id = await _seed_user(session)
    await _ready_for_deploy(session)

    seen: list[str | None] = []

    async def fake_plan(sess, uid, module_name):
        seen.append(await _get_config(sess, "storage_stack_uid"))
        from app.models.component import TerraformRun

        run = TerraformRun(triggered_by_user_id=uid, action="plan", module_name=module_name, status="failed")
        sess.add(run)
        await sess.flush()
        return run

    with patch("app.services.terraform_executor.TerraformExecutor.run_plan", side_effect=fake_plan):
        await deploy_storage_module(session, user_id)

    assert len(seen) == 1 and seen[0] is not None and len(seen[0]) == 6


@pytest.mark.asyncio
async def test_direct_storage_deploy_refuses_an_unidentified_partial_attempt(session):
    from app.api.storage_deploy import deploy_storage_module

    user_id = await _seed_user(session)
    await _ready_for_deploy(session)
    await _record_run(session, user_id, action="apply", status="failed", tfvars={})

    with (
        patch("app.services.terraform_executor.TerraformExecutor.run_plan") as plan,
        pytest.raises(StateError, match="earlier storage deployment"),
    ):
        await deploy_storage_module(session, user_id)
    plan.assert_not_called()


def test_unidentified_partial_refusal_is_shown_to_the_operator():
    """The SSE stream replaces unknown errors with a generic message; this one is actionable."""
    from app.api.stack_deploy import _safe_stack_error_message
    from app.services.storage_generation import UNRESOLVED_PARTIAL_MESSAGE, StorageGenerationUnresolved

    assert _safe_stack_error_message(StorageGenerationUnresolved(UNRESOLVED_PARTIAL_MESSAGE)) == (
        UNRESOLVED_PARTIAL_MESSAGE
    )
