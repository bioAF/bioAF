"""How interruption reaches existing callers.

`interrupted` stays internal: SSE routes send the existing `apply_error` event and
`/terraform/init` returns its existing 500, both with the same message. Deferred
callers keep their current behavior with the additive event.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.terraform_streaming import ClosingStreamingResponse, INTERRUPTED_MESSAGE, client_event
from app.services.terraform_executor import TerraformProgressEvent
from app.services.terraform_stream import owned_event_stream

MESSAGE = "The operation was interrupted before it finished. Check its status, then try again."


def _event(event_type, **kwargs):
    return TerraformProgressEvent(event_type=event_type, **kwargs)


async def _stream(*events):
    for event in events:
        yield event


def _sse_payloads(body: str) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _config(session, **values):
    for key, value in values.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


def test_client_event_translates_only_the_interruption():
    translated = client_event(_event("interrupted", resources_completed=1, resources_total=4))

    assert translated.event_type == "apply_error"
    assert translated.message == MESSAGE == INTERRUPTED_MESSAGE
    assert (translated.resources_completed, translated.resources_total) == (1, 4)
    passthrough = _event("apply_complete", extra={"outputs": {}})
    assert client_event(passthrough) is passthrough


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "target"),
    [
        ("/api/v1/infrastructure/stack/deploy", "app.api.stack_deploy.deploy_stack"),
        ("/api/v1/infrastructure/stack/teardown", "app.api.stack_deploy.teardown_stack"),
        ("/api/v1/infrastructure/stack/destroy-storage", "app.api.stack_deploy.destroy_storage"),
    ],
)
async def test_stack_sse_routes_send_apply_error(client, admin_token, url, target):
    with patch(target, return_value=_stream(_event("progress"), _event("interrupted"))):
        response = await client.post(url, headers=_auth(admin_token))

    payloads = _sse_payloads(response.text)
    assert [p["event_type"] for p in payloads] == ["progress", "apply_error"]
    assert payloads[-1]["message"] == MESSAGE


@pytest.mark.asyncio
async def test_bootstrap_sse_sends_apply_error(client, admin_token, session):
    await _config(session, gcp_credentials_configured="true", terraform_initialized="false")

    with patch(
        "app.api.terraform_executor.TerraformExecutor.bootstrap_foundation",
        return_value=_stream(_event("interrupted")),
    ):
        response = await client.post("/api/v1/infrastructure/terraform/bootstrap", headers=_auth(admin_token))

    assert [(p["event_type"], p["message"]) for p in _sse_payloads(response.text)] == [("apply_error", MESSAGE)]


@pytest.mark.asyncio
async def test_apply_sse_sends_apply_error(client, admin_token, admin_user, session):
    from app.models.component import TerraformRun

    run = TerraformRun(
        triggered_by_user_id=admin_user.id,
        action="plan",
        module_name="storage",
        status="awaiting_confirmation",
    )
    session.add(run)
    await session.commit()

    with patch(
        "app.api.terraform_executor.TerraformExecutor.run_apply",
        return_value=_stream(_event("interrupted")),
    ):
        response = await client.post(f"/api/v1/infrastructure/terraform/apply/{run.id}", headers=_auth(admin_token))

    assert [(p["event_type"], p["message"]) for p in _sse_payloads(response.text)] == [("apply_error", MESSAGE)]


@pytest.mark.asyncio
async def test_billing_teardown_sse_sends_apply_error_and_keeps_config(client, admin_token, session):
    await _config(session, terraform_initialized="true", billing_export_dataset="ds")

    with patch(
        "app.api.billing_export.TerraformExecutor.run_destroy",
        return_value=_stream(_event("interrupted")),
    ):
        response = await client.post(
            "/api/v1/infrastructure/billing-export/teardown",
            headers=_auth(admin_token),
        )

    assert [(p["event_type"], p["message"]) for p in _sse_payloads(response.text)] == [("apply_error", MESSAGE)]
    await session.rollback()
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'billing_export_dataset'"))).one()
    assert row[0] == "ds"


@pytest.mark.asyncio
async def test_terraform_init_returns_500_on_interruption(client, admin_token, session):
    await _config(session, gcp_credentials_configured="true", terraform_initialized="false")

    with patch(
        "app.api.terraform_executor.TerraformExecutor.bootstrap_foundation",
        return_value=_stream(_event("progress"), _event("interrupted")),
    ):
        response = await client.post("/api/v1/infrastructure/terraform/init", headers=_auth(admin_token))

    assert response.status_code == 500
    assert response.json()["detail"] == MESSAGE


@pytest.mark.asyncio
async def test_terraform_init_keeps_its_failure_message(client, admin_token, session):
    await _config(session, gcp_credentials_configured="true", terraform_initialized="false")

    with patch(
        "app.api.terraform_executor.TerraformExecutor.bootstrap_foundation",
        return_value=_stream(_event("apply_error", message="denied")),
    ):
        response = await client.post("/api/v1/infrastructure/terraform/init", headers=_auth(admin_token))

    assert response.status_code == 500
    assert response.json()["detail"] == "denied"


# -----------------------------------------------------------------------
# Deferred callers keep their behavior
# -----------------------------------------------------------------------


def _approved_run():
    return MagicMock(status="awaiting_confirmation", id=1, error_message=None)


@pytest.mark.asyncio
async def test_storage_module_deploy_is_unchanged_by_the_additive_event(session):
    """Its false success on interruption is deferred work, not an oversight here."""
    from app.api.storage_deploy import deploy_storage_module

    with (
        patch("app.services.terraform_executor.TerraformExecutor.run_plan", AsyncMock(return_value=_approved_run())),
        patch(
            "app.services.terraform_executor.TerraformExecutor.run_apply",
            return_value=_stream(_event("interrupted")),
        ),
    ):
        result = await deploy_storage_module(session, 1)

    assert result == {"status": "completed"}


@pytest.mark.asyncio
async def test_billing_module_deploy_is_unchanged_by_the_additive_event(session):
    from app.api.billing_export import deploy_billing_export_module

    with (
        patch("app.api.billing_export.TerraformExecutor.run_plan", AsyncMock(return_value=_approved_run())),
        patch(
            "app.api.billing_export.TerraformExecutor.run_apply",
            return_value=_stream(_event("apply_complete", extra={"outputs": {"dataset_id": {"value": "ds2"}}})),
        ),
    ):
        result = await deploy_billing_export_module(session, 1)

    assert result == {"status": "completed"}


# -----------------------------------------------------------------------
# Background drains and infrastructure updates
# -----------------------------------------------------------------------


@pytest.fixture
def bg_sessions(db_engine, monkeypatch):
    """stack_deploy binds its session factory at import; point it at the test engine."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.api.stack_deploy.async_session_factory", factory)


async def _committed_status(db_engine):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other:
        return (await other.execute(text("SELECT status FROM terraform_runs ORDER BY id"))).scalars().all()


async def _interruptible(session, closed):
    """A stream that holds pending run state until it is cancelled or closed."""
    await session.execute(text("UPDATE terraform_runs SET status = 'failed', error_message = 'interrupted'"))
    await session.flush()
    try:
        while True:
            yield _event("progress")
            await asyncio.sleep(3600)
    finally:
        closed.append(True)


async def _running_run(session, user_id):
    from app.models.component import TerraformRun

    run = TerraformRun(triggered_by_user_id=user_id, action="apply", module_name="storage", status="applying")
    session.add(run)
    await session.commit()
    return run


@pytest.mark.asyncio
async def test_background_drain_commits_interruption_on_cancellation(
    client, admin_user, session, db_engine, bg_sessions
):
    from app.api import stack_deploy

    await _running_run(session, admin_user.id)
    closed = []
    created = []

    def stream(bg_session, *_args, **_kwargs):
        created.append(True)
        return _interruptible(bg_session, closed)

    await _config(session, compute_deployed="true")
    with patch.object(stack_deploy, "teardown_stack", side_effect=stream):
        token = await _admin_token(admin_user)
        response = await client.post("/api/v1/infrastructure/stack/teardown-background", headers=_auth(token))
        assert response.status_code == 200
        while not created:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        teardown = next(t for t in asyncio.all_tasks() if "_run_teardown" in repr(t.get_coro()))
        teardown.cancel()
        with pytest.raises(asyncio.CancelledError):
            await teardown

    assert closed == [True]
    assert await _committed_status(db_engine) == ["failed"]


async def _admin_token(user):
    from app.services.auth_service import AuthService

    return AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name="admin")


@pytest.mark.asyncio
async def test_background_apply_rolls_back_an_unexpected_error(admin_user, session, db_engine, bg_sessions):
    from app.api.stack_deploy import _run_apply_background

    await _running_run(session, admin_user.id)

    async def explode(bg_session, _run_id, _user_id):
        await bg_session.execute(text("UPDATE terraform_runs SET status = 'completed'"))
        yield _event("progress")
        raise RuntimeError("unexpected")

    with patch("app.api.stack_deploy.TerraformExecutor.run_apply", side_effect=explode):
        await _run_apply_background(1, admin_user.id)

    assert await _committed_status(db_engine) == ["applying"]


@pytest.mark.asyncio
async def test_infra_update_stops_the_batch_on_interruption(admin_user, session, db_engine):
    from app.services import infra_update_service

    applied = []

    async def plan(*_args, **_kwargs):
        return MagicMock(status="awaiting_confirmation", id=7, plan_json={}, error_message=None)

    async def apply(_s, _run_id, _user_id, targets):
        applied.append(targets)
        yield _event("interrupted")

    with (
        patch.object(infra_update_service.TerraformExecutor, "run_plan", side_effect=plan),
        patch.object(infra_update_service.TerraformExecutor, "run_apply", side_effect=apply),
        patch.object(infra_update_service, "additive_addresses", return_value=["a.one", "a.two"]),
    ):
        with pytest.raises(asyncio.CancelledError):
            await infra_update_service.apply_modules_sequentially(["storage"], admin_user.id)

    assert applied == [["a.one"]]


@pytest.mark.asyncio
async def test_infra_update_commits_the_outcome_when_cancelled(admin_user, session, db_engine):
    from app.services import infra_update_service

    db_run = await _running_run(session, admin_user.id)
    plan = MagicMock(status="awaiting_confirmation", id=db_run.id, plan_json={}, error_message=None)
    started = asyncio.Event()
    closed = []

    async def apply(s, _run_id, _user_id, targets):
        await s.execute(text("UPDATE terraform_runs SET status = 'failed'"))
        await s.flush()
        try:
            yield _event("progress")
            started.set()
            await asyncio.sleep(3600)
        finally:
            closed.append(True)

    async def drain(events):
        async for _ in events:
            started.set()
            await asyncio.sleep(3600)

    with (
        patch.object(infra_update_service.TerraformExecutor, "run_plan", AsyncMock(return_value=plan)),
        patch.object(infra_update_service.TerraformExecutor, "run_apply", side_effect=apply),
        patch.object(infra_update_service, "additive_addresses", return_value=["a.one"]),
    ):
        task = asyncio.create_task(infra_update_service._apply_one_target("storage", "a.one", admin_user.id))
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert closed == [True]
    assert await _committed_status(db_engine) == ["failed"]


# -----------------------------------------------------------------------
# Client disconnect
# -----------------------------------------------------------------------


def _scope():
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "method": "POST", "path": "/"}


@pytest.mark.asyncio
async def test_disconnect_closes_the_stream_and_runs_owner_cleanup():
    """uvicorn's disconnect cancels the response body without closing it."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    stopped = []
    sent = asyncio.Event()

    async def operation():
        try:
            yield _event("progress")
            await asyncio.sleep(3600)
        finally:
            with anyio.CancelScope(shield=True):
                await asyncio.sleep(0)
                stopped.append(True)

    async def body():
        async with owned_event_stream(session, operation()) as events:
            async for event in events:
                yield f"data: {event.event_type}\n\n"

    async def send(message):
        if message["type"] == "http.response.body":
            sent.set()

    async def receive():
        await sent.wait()
        return {"type": "http.disconnect"}

    await ClosingStreamingResponse(body(), media_type="text/event-stream")(_scope(), receive, send)

    assert stopped == [True]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_while_suspended_at_a_yield_still_closes():
    closed = []
    calls = []

    async def body():
        try:
            yield "data: one\n\n"
            yield "data: two\n\n"
        finally:
            await asyncio.sleep(0)
            closed.append(True)

    disconnect = asyncio.Event()

    async def send(message):
        calls.append(message["type"])
        if message.get("body"):
            disconnect.set()
            await asyncio.sleep(3600)

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    await ClosingStreamingResponse(body(), media_type="text/event-stream")(_scope(), receive, send)

    assert closed == [True]
