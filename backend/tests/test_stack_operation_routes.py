"""Stack actions run as accepted infrastructure operations (storage lifecycle 11D; mockups M2, M6, M7).

Accepted responses carry the operation ID; a second action while one holds exclusion gets 409 with
the running operation; a retry with the same Idempotency-Key replays; a failure before Terraform is
recorded on its own operation, never as another run's success.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from app.models.infrastructure_operation import InfrastructureOperation
from app.services.terraform_executor import TerraformProgressEvent

DEPLOY = "/api/v1/infrastructure/stack/deploy-background"
TEARDOWN = "/api/v1/infrastructure/stack/teardown-background"
DESTROY = "/api/v1/infrastructure/stack/destroy-storage-background"
CHECK = "/api/v1/infrastructure/stack/check-updates"


async def _config(session, **kv):
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()


def _auth(token, key=None):
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


async def _settled(session, op_id: int, timeout: float = 5.0) -> InfrastructureOperation:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        session.expire_all()
        op = (
            await session.execute(select(InfrastructureOperation).where(InfrastructureOperation.id == op_id))
        ).scalar_one()
        if op.exclusive_slot is None or op.status == "recovery_required":
            return op
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"operation {op_id} did not settle: {op.status}")
        await asyncio.sleep(0.02)


def _event(event_type, message=""):
    return TerraformProgressEvent(event_type=event_type, message=message)


@pytest.mark.asyncio
async def test_background_deploy_returns_its_operation_and_records_the_outcome(client, admin_token, session):
    await _config(session, gcp_credentials_configured="true", terraform_initialized="true", compute_deployed="false")

    async def deploy(sess, stack_type, user_id, org_id=None, compute_region=None, compute_zone=None):
        yield _event("progress", "Deploying compute infrastructure...")
        yield _event("stack_complete", "Stack deployed")

    with patch("app.api.stack_deploy.deploy_stack", side_effect=deploy):
        resp = await client.post(DEPLOY, json={"stack_type": "kubernetes"}, headers=_auth(admin_token))
        assert resp.status_code == 200
        op = await _settled(session, resp.json()["operation_id"])

    assert (op.action, op.status, op.message) == ("compute_deploy", "succeeded", "Stack deployed")
    assert [s["key"] for s in op.steps] == ["accepted", "prerequisites", "compute", "cleanup"]


@pytest.mark.asyncio
async def test_a_second_action_gets_409_with_the_running_operation(client, admin_token, session):
    await _config(session, compute_deployed="true", storage_deployed="true", terraform_initialized="true")
    gate = asyncio.Event()

    async def teardown(sess, user_id, org_id=None):
        yield _event("progress", "Tearing down compute...")
        await gate.wait()
        yield _event("stack_complete", "done")

    with patch("app.api.stack_deploy.teardown_stack", side_effect=teardown):
        first = await client.post(TEARDOWN, json={"confirm": True}, headers=_auth(admin_token))
        op_id = first.json()["operation_id"]
        second = await client.post(CHECK, headers=_auth(admin_token))
        gate.set()
        await _settled(session, op_id)

    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "infrastructure_busy"
    assert body["details"]["operation"]["id"] == op_id
    assert body["details"]["operation"]["label"] == "Compute teardown"


@pytest.mark.asyncio
async def test_a_retried_request_with_the_same_key_replays(client, admin_token, session):
    await _config(session, compute_deployed="true")
    gate = asyncio.Event()
    started = []

    async def teardown(sess, user_id, org_id=None):
        started.append(True)
        await gate.wait()
        yield _event("stack_complete", "done")

    with patch("app.api.stack_deploy.teardown_stack", side_effect=teardown):
        a = await client.post(TEARDOWN, json={"confirm": True}, headers=_auth(admin_token, "click-1"))
        b = await client.post(TEARDOWN, json={"confirm": True}, headers=_auth(admin_token, "click-1"))
        await asyncio.sleep(0.05)
        gate.set()
        await _settled(session, a.json()["operation_id"])

    assert b.status_code == 200
    assert a.json()["operation_id"] == b.json()["operation_id"]
    assert started == [True]


@pytest.mark.asyncio
async def test_destroy_failing_before_terraform_is_its_own_failed_operation(client, admin_token, session):
    """M7: an older successful teardown run cannot be shown as this destroy's outcome."""
    from app.services import storage_identity
    from app.services.storage_identity import StoreHealth, StoreReport

    await _config(session, compute_deployed="false", storage_deployed="true", raw_bucket_name="bioaf-raw-acme-4bd459")
    health = iter([StoreHealth.AVAILABLE, StoreHealth.NO_ACCESS])

    async def inspect(sess, store, bucket):
        return StoreReport(store, bucket, next(health), "discovered")

    async def empty(sess, bucket):
        raise RuntimeError("403 https://storage.googleapis.com/b/bioaf-raw-acme-4bd459 SECRET")

    with (
        patch("app.services.stack_deployment._empty_gcs_bucket", side_effect=empty),
        patch.object(storage_identity, "inspect_bucket", side_effect=inspect),
    ):
        resp = await client.post(DESTROY, json={"confirm": True}, headers=_auth(admin_token))
        op = await _settled(session, resp.json()["operation_id"])

    assert op.status == "failed"
    assert op.child_run_ids == []
    assert op.message == (
        "Nothing else was changed. Give the bioAF service account access to bioaf-raw-acme-4bd459 in Google Cloud, "
        "then run Destroy Storage again. It continues from where it stopped. Uploads remain paused until you run "
        "it again or choose Allow uploads."
    )
    assert "SECRET" not in op.message
    step = next(s for s in op.steps if s["key"] == "empty:bioaf-raw-acme-4bd459")
    assert (step["state"], step["detail"]) == (
        "failed",
        "bioAF is not allowed to read this bucket. Nothing was deleted from it.",
    )
    runs = (await session.execute(text("SELECT count(*) FROM terraform_runs"))).scalar_one()
    assert runs == 0


@pytest.mark.asyncio
async def test_check_updates_runs_as_one_background_operation(client, admin_token, session, monkeypatch):
    from app.services import infra_update_service

    await _config(session, terraform_initialized="true", storage_deployed="true")

    async def check(sess, user_id):
        return {
            "has_changes": False,
            "has_additive": False,
            "has_destructive": False,
            "requires_approval": False,
            "realigned": None,
            "modules_with_additive": [],
            "modules": [],
            "additive_resources": [],
            "destructive_resources": [],
        }

    monkeypatch.setattr(infra_update_service, "check_for_updates", check)
    resp = await client.post(CHECK, headers=_auth(admin_token))
    assert resp.status_code == 200
    op = await _settled(session, resp.json()["operation_id"])
    assert (op.action, op.status, op.message) == (
        "infrastructure_update",
        "succeeded",
        "Your infrastructure is up to date.",
    )
    assert [s["key"] for s in op.steps] == ["accepted", "plan"]
