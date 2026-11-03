"""Terminal outcomes at the deployment layer, under real session ownership.

Missing terminal success must not read as success, and an interruption must not
enter the failure branches that record orphans. Session owners close the stream
before ending the transaction, so these tests read results through a second
connection after the owner finished.
"""

import asyncio
import json
from contextlib import aclosing
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.exceptions import DomainError
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.stack_deployment import _run_module, deploy_stack, destroy_storage, teardown_stack
from app.services.terraform_executor import TerraformExecutor, TerraformProgressEvent
from app.services.terraform_stream import owned_event_stream
from tests.test_terraform_interruption import FakeProcess, _hook


def _event(event_type, **kwargs):
    return TerraformProgressEvent(event_type=event_type, **kwargs)


@pytest_asyncio.fixture
async def user_id(session):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="DeployInterruptOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="deploy_interrupt@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.commit()
    return user.id


async def _config(session, **values):
    for key, value in values.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


async def _gcp(session, **overrides):
    await _config(
        session,
        **{
            "gcp_credentials_configured": "true",
            "gcp_credential_source": "vm_default",
            "terraform_initialized": "true",
            "terraform_state_bucket": "bioaf-tfstate",
            "compute_deployed": "false",
            "storage_deployed": "true",
            "gcp_project_id": "proj",
            "gcp_region": "us-central1",
            "gcp_zone": "us-central1-a",
            "org_slug": "acme",
            **overrides,
        },
    )


async def _committed(db_engine, key):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other:
        row = (
            await other.execute(text("SELECT value FROM platform_config WHERE key = :k").bindparams(k=key))
        ).fetchone()
    return row[0] if row else None


async def _orphan_count(session):
    return (await session.execute(text("SELECT COUNT(*) FROM orphaned_resources"))).scalar()


@pytest.fixture(autouse=True)
def _probe(monkeypatch):
    monkeypatch.setattr(
        "app.services.stack_deployment._select_default_pool_zone",
        AsyncMock(return_value="us-central1-a"),
    )


def _script(scripts):
    async def run(_session, _user_id, module_name):
        for event in scripts.get(module_name, [_event("apply_complete")]):
            yield event

    return run


# -----------------------------------------------------------------------
# Missing terminal success
# -----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [[], [_event("interrupted")]], ids=["exhausted", "interrupted"])
async def test_storage_without_success_stops_the_stack(session, user_id, db_engine, terminal):
    await _gcp(session, storage_deployed="false")

    with patch("app.services.stack_deployment._run_module", side_effect=_script({"storage": terminal})):
        events = [e async for e in deploy_stack(session, "kubernetes", user_id=user_id)]
    await session.commit()

    assert events[-1].event_type == "interrupted"
    assert not any(e.event_type in ("stack_complete", "stack_error", "phase_complete") for e in events)
    assert await _committed(db_engine, "storage_deployed") == "false"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [[], [_event("interrupted")]], ids=["exhausted", "interrupted"])
async def test_compute_without_success_restores_location_and_records_no_orphans(session, user_id, db_engine, terminal):
    await _gcp(session)

    with patch("app.services.stack_deployment._run_module", side_effect=_script({"compute": terminal})):
        events = [e async for e in deploy_stack(session, "kubernetes", user_id=user_id, compute_region="europe-west1")]
    await session.commit()

    assert [e.event_type for e in events if e.event_type == "interrupted"] == ["interrupted"]
    assert events[-1].event_type == "interrupted"
    assert not any(e.event_type in ("stack_complete", "stack_error") for e in events)
    assert await _committed(db_engine, "gcp_region") == "us-central1"
    assert await _committed(db_engine, "gcp_zone") == "us-central1-a"
    assert await _committed(db_engine, "compute_deployed") == "false"
    assert await _orphan_count(session) == 0


@pytest.mark.asyncio
async def test_aws_image_build_without_success_stops_before_compute(session, user_id):
    await _config(
        session,
        cloud_provider="aws",
        aws_account_id="111122223333",
        aws_region="us-west-1",
        terraform_initialized="true",
        compute_deployed="false",
        storage_deployed="true",
    )

    with patch("app.services.stack_deployment._run_module", side_effect=_script({"image_build": []})):
        events = [e async for e in deploy_stack(session, "kubernetes", user_id=user_id)]

    assert events[-1].event_type == "interrupted"
    assert not any(e.message == "Deploying compute infrastructure..." for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [[], [_event("interrupted")]], ids=["exhausted", "interrupted"])
async def test_teardown_without_success_keeps_identity(session, user_id, db_engine, terminal):
    await _gcp(session, compute_deployed="true", gke_cluster_name="bioaf-acme-abc123", gke_cluster_endpoint="https://x")

    async def destroy(_s, _u, _m):
        for event in terminal:
            yield event

    with patch("app.services.stack_deployment._run_destroy", side_effect=destroy):
        events = [e async for e in teardown_stack(session, user_id=user_id)]
    await session.commit()

    assert events[-1].event_type == "interrupted"
    assert len([e for e in events if e.event_type == "interrupted"]) == 1
    assert not any(e.event_type in ("stack_complete", "stack_error") for e in events)
    assert await _committed(db_engine, "compute_deployed") == "true"
    assert await _committed(db_engine, "gke_cluster_name") == "bioaf-acme-abc123"
    assert await _committed(db_engine, "gke_cluster_endpoint") == "https://x"
    assert await _orphan_count(session) == 0


@pytest.mark.asyncio
async def test_storage_destroy_without_success_keeps_identity(session, user_id, db_engine):
    await _gcp(session, raw_bucket_name="bioaf-raw")

    async def destroy(_s, _u, _m):
        yield _event("progress")

    with (
        patch("app.services.stack_deployment._run_destroy", side_effect=destroy),
        patch("app.services.stack_deployment._empty_gcs_bucket", AsyncMock(return_value=0)),
    ):
        events = [e async for e in destroy_storage(session, user_id=user_id)]
    await session.commit()

    assert events[-1].event_type == "interrupted"
    assert await _committed(db_engine, "storage_deployed") == "true"
    assert await _committed(db_engine, "raw_bucket_name") == "bioaf-raw"


@pytest.mark.asyncio
async def test_image_build_destroy_without_success_keeps_identity(session, user_id, db_engine):
    await _config(
        session,
        cloud_provider="aws",
        aws_image_build_deployed="true",
        aws_codebuild_project="bioaf-build",
        compute_deployed="false",
        storage_deployed="true",
    )
    modules = []

    async def destroy(_s, _u, module_name):
        modules.append(module_name)
        yield _event("progress")

    with patch("app.services.stack_deployment._run_destroy", side_effect=destroy):
        events = [e async for e in destroy_storage(session, user_id=user_id)]
    await session.commit()

    assert modules == ["image_build"]
    assert events[-1].event_type == "interrupted"
    assert await _committed(db_engine, "aws_codebuild_project") == "bioaf-build"
    assert await _committed(db_engine, "aws_image_build_deployed") == "true"


# -----------------------------------------------------------------------
# Plan interruption does not commit for the caller
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_cancellation_marks_the_run_without_committing_pending_work(session, user_id, db_engine):
    await _gcp(session)

    async def cancelled_plan(sess, _user_id, module_name):
        await sess.execute(
            text(
                "INSERT INTO terraform_runs (triggered_by_user_id, action, module_name, status) "
                "VALUES (:u, 'plan', :m, 'planning')"
            ).bindparams(u=user_id, m=module_name)
        )
        await sess.flush()
        raise asyncio.CancelledError

    await session.execute(text("UPDATE platform_config SET value = 'pending' WHERE key = 'org_slug'"))

    with patch.object(TerraformExecutor, "run_plan", side_effect=cancelled_plan):
        events = [e async for e in _run_module(session, user_id, "compute")]

    assert [e.event_type for e in events] == ["interrupted"]
    row = (await session.execute(text("SELECT status, error_message FROM terraform_runs"))).fetchone()
    assert row.status == "failed"
    assert await _committed(db_engine, "org_slug") == "acme"
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other:
        assert (await other.execute(text("SELECT COUNT(*) FROM terraform_runs"))).scalar() == 0


@pytest.mark.asyncio
async def test_plan_failure_is_not_an_interruption(session, user_id):
    await _gcp(session)

    with patch.object(
        TerraformExecutor,
        "run_plan",
        AsyncMock(return_value=MagicMock(status="failed", error_message="bad plan", id=1)),
    ):
        events = [e async for e in _run_module(session, user_id, "compute")]

    assert [(e.event_type, e.message) for e in events] == [("apply_error", "bad plan")]


# -----------------------------------------------------------------------
# Closure through nested generators
# -----------------------------------------------------------------------


@pytest.fixture
def real_module(monkeypatch):
    """Let `_run_module` run the real plan and apply against a fake Terraform."""
    process = FakeProcess([_hook("a.one")])
    show = MagicMock(
        returncode=0,
        stdout=json.dumps(
            {
                "resource_changes": [
                    {
                        "address": "a.one",
                        "type": "a",
                        "name": "one",
                        "change": {"actions": ["create"], "before": None, "after": {}},
                    }
                ]
            }
        ),
        stderr="",
    )
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr("app.services.terraform_executor.subprocess.run", lambda *a, **k: show)
    monkeypatch.setattr(TerraformExecutor, "_delete_gcs_lock_file", staticmethod(AsyncMock()))
    monkeypatch.setattr(TerraformExecutor, "_load_gcs_credentials", AsyncMock(return_value=None))
    import tempfile
    from pathlib import Path

    work_dir = Path(tempfile.mkdtemp(prefix="tf_nested_"))
    monkeypatch.setattr(TerraformExecutor, "_prepare_work_dir", staticmethod(lambda *a: work_dir))
    return process


async def _terminal_state(db_engine):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other:
        return (
            await other.execute(text("SELECT status FROM terraform_runs WHERE module_name = 'compute'"))
        ).scalar_one()


@pytest.mark.asyncio
async def test_closing_the_outer_stream_stops_terraform_and_owner_commits(session, user_id, db_engine, real_module):
    await _gcp(session)
    stream = deploy_stack(session, "kubernetes", user_id=user_id, compute_region="europe-west1")

    async with owned_event_stream(session, stream) as events:
        async for event in events:
            if event.event_type == "resource_complete":
                break

    assert real_module.signals == ["term"]
    assert await _terminal_state(db_engine) == "failed"
    assert await _committed(db_engine, "gcp_region") == "us-central1"
    assert await _orphan_count(session) == 0
    phase = (await session.execute(text("SELECT deploy_phase FROM terraform_runs"))).scalar_one()
    assert phase == "compute"


@pytest.mark.asyncio
async def test_task_cancellation_through_nested_generators(session, user_id, db_engine, real_module):
    await _gcp(session)
    collected = []

    async def owner():
        stream = deploy_stack(session, "kubernetes", user_id=user_id, compute_region="europe-west1")
        async with owned_event_stream(session, stream) as events:
            async for event in events:
                collected.append(event)

    task = asyncio.create_task(owner())
    while not real_module.reading.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    await task

    assert real_module.signals == ["term"]
    assert collected[-1].event_type == "interrupted"
    assert await _terminal_state(db_engine) == "failed"
    assert await _committed(db_engine, "gcp_region") == "us-central1"
    assert await _orphan_count(session) == 0


@pytest.mark.asyncio
async def test_cancelled_scope_through_nested_generators(session, user_id, db_engine, real_module):
    await _gcp(session)
    scope_holder = []

    async def owner():
        with anyio.CancelScope() as scope:
            scope_holder.append(scope)
            stream = deploy_stack(session, "kubernetes", user_id=user_id, compute_region="europe-west1")
            async with owned_event_stream(session, stream) as events:
                async for _ in events:
                    pass

    task = asyncio.create_task(owner())
    while not real_module.reading.is_set():
        await asyncio.sleep(0.001)
    scope_holder[0].cancel()
    await task

    assert real_module.signals == ["term"]
    assert await _terminal_state(db_engine) == "failed"
    assert await _committed(db_engine, "gcp_region") == "us-central1"


@pytest.mark.asyncio
async def test_probe_cancellation_restores_the_override(session, user_id, db_engine):
    await _gcp(session)

    with patch(
        "app.services.stack_deployment._select_default_pool_zone",
        AsyncMock(side_effect=asyncio.CancelledError),
    ):
        events = [e async for e in deploy_stack(session, "kubernetes", user_id=user_id, compute_region="europe-west1")]
    await session.commit()

    assert events[-1].event_type == "interrupted"
    assert await _committed(db_engine, "gcp_region") == "us-central1"


@pytest.mark.asyncio
async def test_unexpected_error_rolls_back_through_the_owner(session, user_id, db_engine):
    await _gcp(session, storage_deployed="false")

    async def explode(_s, _u, _m):
        yield _event("apply_complete")

    with (
        patch("app.services.stack_deployment._run_module", side_effect=explode),
        patch("app.services.stack_deployment.process_queued_components", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        with pytest.raises(RuntimeError):
            async with owned_event_stream(session, deploy_stack(session, "kubernetes", user_id=user_id)) as events:
                async for _ in events:
                    pass

    assert await _committed(db_engine, "storage_deployed") == "false"
    await session.rollback()


@pytest.mark.asyncio
async def test_domain_errors_still_reach_the_route(session, user_id):
    await _gcp(session, terraform_initialized="false")

    with pytest.raises(DomainError):
        async with aclosing(deploy_stack(session, "kubernetes", user_id=user_id)) as events:
            async for _ in events:
                pass


@pytest.mark.asyncio
async def test_cancellation_inside_the_compute_hook_is_not_success(session, user_id, db_engine):
    await _gcp(session)

    with (
        patch("app.services.stack_deployment._run_module", side_effect=_script({})),
        patch(
            "app.services.stack_deployment.process_queued_components",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
    ):
        events = [e async for e in deploy_stack(session, "kubernetes", user_id=user_id, compute_region="europe-west1")]
    await session.commit()

    assert events[-1].event_type == "interrupted"
    assert not any(e.event_type == "stack_complete" for e in events)
    assert await _committed(db_engine, "gcp_region") == "us-central1"


@pytest.mark.asyncio
async def test_cancellation_inside_a_hook_never_commits_half_of_it(session, user_id, db_engine):
    """A confirmed apply's post-hook is recorded whole or not at all."""
    await _gcp(session, storage_deployed="false")

    async def cancel_midway(_session):
        raise asyncio.CancelledError

    with (
        patch("app.services.stack_deployment._run_module", side_effect=_script({})),
        patch("app.services.stack_deployment.process_queued_components", side_effect=cancel_midway),
    ):
        # Outside compute, cancellation propagates to the owner after it commits.
        with pytest.raises(asyncio.CancelledError):
            async with owned_event_stream(session, deploy_stack(session, "kubernetes", user_id=user_id)) as stream:
                async for _ in stream:
                    pass

    storage = await _committed(db_engine, "storage_deployed")
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other:
        audits = (await other.execute(text("SELECT COUNT(*) FROM audit_log WHERE action = 'deploy_storage'"))).scalar()
    assert (storage, audits) in {("false", 0), ("true", 1)}
