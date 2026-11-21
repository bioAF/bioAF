"""Durable infrastructure operations (storage lifecycle stage 11D; approved D1, D2, D4).

One accepted operation owns an infrastructure workflow from admission to its settled outcome.
Admission is atomic and installation-wide; replays and conflicts follow ADR-050; outcomes are
truthful (no terminal event never means success); restart recovery uses recorded runner evidence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from app.exceptions import StateError, ValidationError
from app.models.infrastructure_operation import InfrastructureOperation
from app.services import infrastructure_operations as ops
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.terraform_stream import INTERRUPTED_EVENT, TerraformProgressEvent


async def _seed_user(session, email="ops@test.com") -> int:
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = (await session.execute(select(Organization).limit(1))).scalar_one_or_none()
    if org is None:
        org = Organization(name="OpsOrg", setup_complete=True)
        session.add(org)
        await session.flush()
        roles = await seed_builtin_roles(session, org.id)
    else:
        from app.models.role import Role

        roles = {"admin": (await session.execute(select(Role.id).where(Role.name == "admin"))).scalar_one()}
    user = User(
        email=email,
        name=email.split("@")[0],
        password_hash=AuthService.hash_password("pw"),
        role_id=roles["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user.id


async def _op(session, op_id: int) -> InfrastructureOperation:
    session.expire_all()
    return (
        await session.execute(select(InfrastructureOperation).where(InfrastructureOperation.id == op_id))
    ).scalar_one()


def _event(event_type: str, message: str = "", **kw) -> TerraformProgressEvent:
    return TerraformProgressEvent(event_type=event_type, message=message, **kw)


# ---------------------------------------------------------------------------
# admission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accepting_takes_installation_wide_exclusion(session):
    user_id = await _seed_user(session)
    op_id, replayed = await ops.accept(action="compute_teardown", principal=ops.Principal(user_id))

    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot, replayed) == ("accepted", 1, False)
    assert op.steps == [{"key": "accepted", "label": "Accepted", "state": "done"}]


@pytest.mark.asyncio
async def test_a_second_action_is_refused_with_the_running_operation(session):
    a = await _seed_user(session, "admin_a@test.com")
    b = await _seed_user(session, "admin_b@test.com")
    first, _ = await ops.accept(action="compute_teardown", principal=ops.Principal(a))

    with pytest.raises(ops.InfrastructureBusyError) as caught:
        await ops.accept(action="infrastructure_update", principal=ops.Principal(b))

    assert caught.value.status_code == 409
    operation = caught.value.details["operation"]
    assert (operation["id"], operation["label"], operation["started_by"]) == (first, "Compute teardown", "admin_a")
    assert "Infrastructure update was not started" in str(caught.value)
    count = (await session.execute(text("SELECT count(*) FROM infrastructure_operations"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_simultaneous_confirmations_admit_exactly_one(session):
    a = await _seed_user(session, "admin_a@test.com")
    b = await _seed_user(session, "admin_b@test.com")

    results = await asyncio.gather(
        ops.accept(action="compute_teardown", principal=ops.Principal(a)),
        ops.accept(action="storage_destroy", principal=ops.Principal(b)),
        return_exceptions=True,
    )

    accepted = [r for r in results if isinstance(r, tuple)]
    refused = [r for r in results if isinstance(r, ops.InfrastructureBusyError)]
    assert len(accepted) == 1 and len(refused) == 1


@pytest.mark.asyncio
async def test_same_key_and_inputs_replay_the_same_operation(session):
    user_id = await _seed_user(session)
    principal = ops.Principal(user_id)
    first, _ = await ops.accept(action="compute_deploy", principal=principal, idempotency_key="k1", inputs={"r": "x"})
    again, replayed = await ops.accept(
        action="compute_deploy", principal=principal, idempotency_key="k1", inputs={"r": "x"}
    )
    assert (again, replayed) == (first, True)


@pytest.mark.asyncio
async def test_same_key_with_changed_inputs_is_refused_422(session):
    user_id = await _seed_user(session)
    principal = ops.Principal(user_id)
    await ops.accept(action="compute_deploy", principal=principal, idempotency_key="k1", inputs={"r": "x"})
    with pytest.raises(ops.IdempotencyKeyReusedError) as caught:
        await ops.accept(action="compute_deploy", principal=principal, idempotency_key="k1", inputs={"r": "y"})
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_a_key_is_scoped_to_its_principal(session):
    a = await _seed_user(session, "admin_a@test.com")
    b = await _seed_user(session, "admin_b@test.com")
    await ops.accept(action="compute_deploy", principal=ops.Principal(a), idempotency_key="shared")
    with pytest.raises(ops.InfrastructureBusyError):
        await ops.accept(action="compute_deploy", principal=ops.Principal(b), idempotency_key="shared")


@pytest.mark.asyncio
async def test_an_active_operation_replays_after_the_cache_window(session):
    user_id = await _seed_user(session)
    principal = ops.Principal(user_id)
    first, _ = await ops.accept(action="compute_deploy", principal=principal, idempotency_key="old")
    await session.execute(
        text("UPDATE infrastructure_operations SET accepted_at = now() - interval '3 days' WHERE id = :i").bindparams(
            i=first
        )
    )
    await session.commit()
    again, replayed = await ops.accept(action="compute_deploy", principal=principal, idempotency_key="old")
    assert (again, replayed) == (first, True)


@pytest.mark.asyncio
async def test_a_new_attempt_after_a_settled_failure_gets_a_new_operation(session):
    user_id = await _seed_user(session)
    first, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    await ops.finish(first, ops.failed("Capacity", "stack_error"))
    second, replayed = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    assert second != first and replayed is False


# ---------------------------------------------------------------------------
# running and outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_releases_exclusion_and_records_the_runner(session):
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="compute_teardown", principal=ops.Principal(user_id))

    async def body(handle):
        assert ops.current_operation_id() == op_id
        await handle.step("work", "Removing compute resources")
        return ops.succeeded("Done")

    await ops.run_operation(op_id, body)

    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot, op.message) == ("succeeded", None, "Done")
    assert op.runner["container_id"] and op.runner["pid"]
    assert [s["state"] for s in op.steps] == ["done", "done"]
    assert ops.current_operation_id() is None


@pytest.mark.asyncio
async def test_a_domain_failure_is_recorded_with_its_safe_message(session):
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="compute_teardown", principal=ops.Principal(user_id))

    async def body(handle):
        await handle.step("prerequisites", "Checking prerequisites")
        raise StateError("Compute stack is not deployed")

    await ops.run_operation(op_id, body)

    op = await _op(session, op_id)
    assert (op.status, op.message, op.error_category, op.exclusive_slot) == (
        "failed",
        "Compute stack is not deployed",
        "invalid_state",
        None,
    )
    assert op.steps[-1]["state"] == "failed"


@pytest.mark.asyncio
async def test_an_unexpected_failure_never_records_raw_error_text(session):
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(user_id))

    async def body(handle):
        raise RuntimeError("403 GET https://storage.googleapis.com/b/secret?token=SECRET")

    await ops.run_operation(op_id, body)

    op = await _op(session, op_id)
    assert op.status == "failed"
    assert "SECRET" not in (op.message or "") and "googleapis" not in (op.message or "")


@pytest.mark.asyncio
async def test_an_unsettled_process_requires_recovery_and_keeps_exclusion(session):
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(user_id))

    async def body(handle):
        raise ops.ProcessNotSettled("terraform plan did not exit")

    await ops.run_operation(op_id, body)

    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot) == ("recovery_required", 1)
    with pytest.raises(ops.InfrastructureBusyError):
        await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))


@pytest.mark.asyncio
async def test_progress_without_a_terminal_event_is_never_success(session):
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))

    async def events(_session):
        yield _event("progress", "Deploying compute infrastructure...")
        yield _event("resource_complete", resources_completed=3, resources_total=9)

    await ops.run_operation(op_id, lambda h: ops.drive_stack(h, events))

    op = await _op(session, op_id)
    assert op.status == "failed" and op.error_category == "no_outcome"


@pytest.mark.asyncio
async def test_stack_events_become_operation_steps(session):
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))

    async def events(_session):
        yield _event("progress", "Deploying storage infrastructure...")
        yield _event("phase_complete", resources_completed=4, resources_total=4)
        yield _event("progress", "Deploying compute infrastructure...")
        yield _event("resource_complete", resources_completed=12, resources_total=31)
        yield _event("stack_error", "Stack deployment failed during compute module")

    await ops.run_operation(op_id, lambda h: ops.drive_stack(h, events))

    op = await _op(session, op_id)
    steps = {s["key"]: s for s in op.steps}
    assert steps["prerequisites"]["state"] == "done"
    assert steps["storage"] == {
        "key": "storage",
        "label": "Creating storage resources",
        "state": "done",
        "completed": 4,
        "total": 4,
    }
    assert (steps["compute"]["state"], steps["compute"]["completed"], steps["compute"]["total"]) == ("failed", 12, 31)
    assert (op.status, op.message) == ("failed", "Stack deployment failed during compute module")


@pytest.mark.asyncio
async def test_failure_before_terraform_is_recorded_on_its_own_operation(session):
    """M7: no Terraform run exists, and an older run's success cannot stand in."""
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="storage_destroy", principal=ops.Principal(user_id))

    async def events(_session):
        yield _event("progress", "Emptying bucket bioaf-raw-acme-4bd459...")
        yield _event("stack_error", "bioAF is not allowed to read this bucket. Nothing was deleted from it.")

    await ops.run_operation(op_id, lambda h: ops.drive_stack(h, events))

    op = await _op(session, op_id)
    assert op.status == "failed"
    assert op.child_run_ids == []
    empty = next(s for s in op.steps if s["key"] == "empty:bioaf-raw-acme-4bd459")
    assert (empty["label"], empty["state"]) == ("Emptying bioaf-raw-acme-4bd459", "failed")


@pytest.mark.asyncio
async def test_interrupted_stack_records_interrupted(session):
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))

    async def events(_session):
        yield _event("progress", "Deploying compute infrastructure...")
        yield _event(INTERRUPTED_EVENT)

    await ops.run_operation(op_id, lambda h: ops.drive_stack(h, events))
    assert (await _op(session, op_id)).status == "interrupted"


@pytest.mark.asyncio
async def test_terraform_outside_an_operation_is_refused_while_one_runs(session):
    from app.services.terraform_executor import TerraformExecutor

    user_id = await _seed_user(session)
    await ops.accept(action="compute_teardown", principal=ops.Principal(user_id))

    with pytest.raises(ops.InfrastructureBusyError):
        await TerraformExecutor.run_plan(session, user_id, module_name="storage")
    with pytest.raises(ops.InfrastructureBusyError):
        async for _ in TerraformExecutor.run_destroy(session, user_id, "storage"):
            pass


@pytest.mark.asyncio
async def test_terraform_runs_inside_an_operation_become_its_children(session):
    from app.models.component import TerraformRun
    from app.services.terraform_executor import TerraformExecutor

    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="infrastructure_update", principal=ops.Principal(user_id))
    seen: list[int] = []

    async def body(handle):
        async with __import__("app.database", fromlist=["x"]).async_session_factory() as s:
            run = TerraformRun(triggered_by_user_id=user_id, action="plan", module_name="storage", status="planning")
            s.add(run)
            await s.flush()
            await TerraformExecutor._join_operation(s, run)
            await s.commit()
            seen.append(run.id)
        return ops.succeeded()

    await ops.run_operation(op_id, body)

    op = await _op(session, op_id)
    assert op.child_run_ids == seen
    run = (await session.execute(select(TerraformRun).where(TerraformRun.id == seen[0]))).scalar_one()
    assert run.operation_id == op_id


# ---------------------------------------------------------------------------
# restart recovery (D1)
# ---------------------------------------------------------------------------

RUNNER = {"container_id": "abc123def456", "container_start": "boot-1:100", "pid": 7}


async def _running(session, user_id, runner=RUNNER, external=None) -> int:
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    await session.execute(
        text(
            "UPDATE infrastructure_operations SET status='running', runner=CAST(:r AS jsonb), "
            "external_builds=CAST(:e AS jsonb) WHERE id=:i"
        ).bindparams(
            r=__import__("json").dumps(runner) if runner else None,
            e=__import__("json").dumps(external or []),
            i=op_id,
        )
    )
    await session.commit()
    return op_id


@pytest.mark.asyncio
async def test_a_confirmed_container_restart_interrupts_and_releases(session):
    from app.models.component import TerraformRun

    user_id = await _seed_user(session)
    op_id = await _running(session, user_id)
    run = TerraformRun(
        triggered_by_user_id=user_id, action="apply", module_name="compute", status="applying", operation_id=op_id
    )
    session.add(run)
    await session.flush()
    run_id = run.id
    await session.execute(
        text("UPDATE infrastructure_operations SET child_run_ids = CAST(:c AS jsonb) WHERE id=:i").bindparams(
            c=f"[{run_id}]", i=op_id
        )
    )
    await session.commit()

    now = {**RUNNER, "container_start": "boot-1:999", "pid": 1}
    with patch("app.services.infrastructure_operations.current_runner", return_value=now):
        counts = await ops.reconcile_on_startup(session)
    await session.commit()

    op = await _op(session, op_id)
    assert counts["interrupted"] == 1
    assert (op.status, op.exclusive_slot) == ("interrupted", None)
    assert op.message == "The bioAF server restarted while this was running, and Terraform stopped with it."
    assert (
        await session.execute(select(TerraformRun.status).where(TerraformRun.id == run_id))
    ).scalar_one() == "failed"


@pytest.mark.parametrize(
    "now",
    [
        {"container_id": "other-container", "container_start": "boot-1:999", "pid": 1},  # another container
        {**RUNNER, "pid": 99},  # same container start: only the server process restarted
        {**RUNNER, "container_start": None},  # container start unreadable
    ],
)
@pytest.mark.asyncio
async def test_without_proof_the_operation_requires_recovery(session, now):
    user_id = await _seed_user(session)
    op_id = await _running(session, user_id)
    with patch("app.services.infrastructure_operations.current_runner", return_value=now):
        await ops.reconcile_on_startup(session)
    await session.commit()

    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot) == ("recovery_required", 1)


@pytest.mark.asyncio
async def test_accepted_work_that_never_started_is_interrupted_with_nothing_changed(session):
    user_id = await _seed_user(session)
    op_id, _ = await ops.accept(action="compute_deploy", principal=ops.Principal(user_id))
    await ops.reconcile_on_startup(session)
    await session.commit()
    op = await _op(session, op_id)
    assert (op.status, op.error_category, op.exclusive_slot) == ("interrupted", "not_started", None)


@pytest.mark.asyncio
async def test_an_external_build_waits_for_its_monitor(session):
    user_id = await _seed_user(session)
    op_id = await _running(session, user_id, external=[{"kind": "notebook", "build_id": "b-1"}])
    with patch(
        "app.services.infrastructure_operations.current_runner", return_value={**RUNNER, "container_start": "x"}
    ):
        await ops.reconcile_on_startup(session)
    await session.commit()
    assert (await _op(session, op_id)).status == "running"


# ---------------------------------------------------------------------------
# administrator recovery (M10)
# ---------------------------------------------------------------------------


async def _needing_recovery(session, user_id) -> int:
    op_id = await _running(session, user_id, runner={**RUNNER, "container_id": "gone"})
    await ops.reconcile_on_startup(session)
    await session.commit()
    return op_id


@pytest.mark.asyncio
async def test_recovery_is_audited_and_releases_exclusion(session):
    user_id = await _seed_user(session)
    op_id = await _needing_recovery(session, user_id)

    await ops.recover(session, op_id, user_id, reason="VM checked, no terraform process")
    await session.commit()

    op = await _op(session, op_id)
    assert (op.status, op.exclusive_slot, op.recovered_by_user_id) == ("interrupted", None, user_id)
    assert op.recovery_reason == "VM checked, no terraform process"
    audit = (
        await session.execute(text("SELECT count(*) FROM audit_log WHERE action='infrastructure_operation_recovered'"))
    ).scalar_one()
    assert audit == 1


@pytest.mark.asyncio
async def test_recovery_is_refused_while_the_process_is_seen(session):
    user_id = await _seed_user(session)
    op_id = await _needing_recovery(session, user_id)

    with (
        patch("app.services.terraform_executor.live_child_pids", return_value=[4182]),
        patch("app.services.infrastructure_operations.pid_alive", return_value=True),
        pytest.raises(StateError, match="still sees this Terraform process running"),
    ):
        await ops.recover(session, op_id, user_id, reason="checked")
    assert (await _op(session, op_id)).status == "recovery_required"


@pytest.mark.asyncio
async def test_recovery_needs_a_reason(session):
    user_id = await _seed_user(session)
    op_id = await _needing_recovery(session, user_id)
    with pytest.raises(ValidationError):
        await ops.recover(session, op_id, user_id, reason="  ")


@pytest.mark.asyncio
async def test_a_running_operation_cannot_be_marked_recovered(session):
    user_id = await _seed_user(session)
    op_id = await _running(session, user_id)
    with pytest.raises(StateError, match="Only an operation that needs recovery"):
        await ops.recover(session, op_id, user_id, reason="checked")


@pytest.mark.asyncio
async def test_abandon_refuses_a_run_owned_by_an_active_operation(session):
    from app.models.component import TerraformRun
    from app.services.terraform_executor import TerraformExecutor

    user_id = await _seed_user(session)
    op_id = await _running(session, user_id)
    run = TerraformRun(
        triggered_by_user_id=user_id, action="apply", module_name="compute", status="applying", operation_id=op_id
    )
    session.add(run)
    await session.commit()

    with pytest.raises(StateError, match=f"Operation #{op_id}"):
        await TerraformExecutor.abandon_run(session, run.id, user_id)


@pytest.mark.asyncio
async def test_stale_run_recovery_leaves_another_operations_runs_alone(session):
    from app.models.component import TerraformRun
    from app.services.terraform_executor import TerraformExecutor

    user_id = await _seed_user(session)
    op_id = await _running(session, user_id)
    owned = TerraformRun(
        triggered_by_user_id=user_id, action="apply", module_name="compute", status="applying", operation_id=op_id
    )
    legacy = TerraformRun(triggered_by_user_id=user_id, action="apply", module_name="storage", status="applying")
    session.add_all([owned, legacy])
    await session.commit()

    with patch.object(TerraformExecutor, "_resolve_cloud_and_config", return_value=(_NoLockCloud(), {})):
        await TerraformExecutor._recover_stale_runs(session)
    await session.commit()

    statuses = dict((await session.execute(select(TerraformRun.id, TerraformRun.status))).all())
    assert statuses[owned.id] == "applying"
    assert statuses[legacy.id] == "failed"


class _NoLockCloud:
    def lock_object_path(self, module_name):
        return None


def test_now_helper_is_timezone_aware():
    assert ops._now().tzinfo is not None and ops._now() <= datetime.now(timezone.utc) + timedelta(seconds=1)
