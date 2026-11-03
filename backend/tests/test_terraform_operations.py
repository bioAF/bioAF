"""Observable behavior of the three streaming Terraform operations.

Apply, foundation bootstrap, and destroy share process mechanics but classify
Terraform's JSON output differently, so each classification is pinned here. The
process is a scripted stand-in; test_terraform_stream.py covers real processes.
"""

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.models.component import TerraformRun
from app.services.bootstrap_roles import seed_builtin_roles
from app.services.terraform_executor import TerraformExecutor


def _hook(kind, addr):
    return json.dumps({"type": kind, "@message": f"{addr}: {kind}", "hook": {"resource": {"addr": addr}}})


def _process(lines, returncode=0, stderr=""):
    queue = [f"{line}\n".encode() for line in lines]

    async def readline():
        return queue.pop(0) if queue else b""

    process = MagicMock()
    process.stdout.readline = readline
    process.stderr.read = AsyncMock(return_value=stderr.encode())
    process.returncode = returncode
    process.wait = AsyncMock(return_value=returncode)
    return process


@contextmanager
def _terraform(process, outputs=None):
    """Patch process creation, the work dir, and every blocking terraform call."""
    work_dir = Path(tempfile.mkdtemp(prefix="tf_ops_"))
    completed = MagicMock(returncode=0, stdout=json.dumps(outputs or {}), stderr="")
    show = MagicMock(
        returncode=0,
        stdout=json.dumps({"resource_changes": []}),
        stderr="",
    )

    def run(cmd, **_kwargs):
        return show if "show" in cmd else completed

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch("app.services.terraform_executor.subprocess.run", side_effect=run),
        patch.object(TerraformExecutor, "_prepare_work_dir", return_value=work_dir),
    ):
        yield work_dir


async def _seed(session, initialized=True):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="OpsOrg", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)
    user = User(
        email="ops@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    for key, value in {
        "gcp_credentials_configured": "true",
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "proj",
        "gcp_region": "us-central1",
        "terraform_initialized": "true" if initialized else "false",
        "terraform_state_bucket": "bioaf-tfstate" if initialized else "",
    }.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()
    return user.id


async def _apply_run(session, user_id, planned=2, addresses=("a.one", "a.two")):
    run = TerraformRun(
        triggered_by_user_id=user_id,
        action="plan",
        module_name="storage",
        status="awaiting_confirmation",
        resources_planned=planned,
        plan_json={"resources": [{"address": a} for a in addresses]},
    )
    session.add(run)
    await session.commit()
    return run


def _types(events):
    return [e.event_type for e in events]


@pytest.mark.asyncio
async def test_apply_classification(session):
    user_id = await _seed(session)
    run = await _apply_run(session, user_id, addresses=("a.one", "a.two", "data.skipped"))
    process = _process(
        [
            "not json",
            "",
            _hook("apply_start", "data.d"),
            _hook("apply_complete", "data.d"),
            _hook("apply_start", "a.one"),
            _hook("apply_complete", "a.one"),
            json.dumps({"type": "planned_change", "@message": "plan a.two"}),
            json.dumps({"type": "other"}),
        ]
    )

    with _terraform(process, outputs={"bucket": {"value": "b"}}):
        events = [e async for e in TerraformExecutor.run_apply(session, run.id, user_id)]

    assert _types(events) == [
        "planned_resources",
        "progress",  # read of data.d, not counted
        "progress",  # start of a.one
        "resource_complete",
        "progress",  # any other message
        "apply_complete",
    ]
    assert events[0].extra == {"addresses": ["a.one", "a.two"]}
    assert events[1].message == "Read: data.d"
    assert (events[3].resource_address, events[3].message) == ("a.one", "Applied: a.one")
    assert (events[3].resources_completed, events[3].resources_total) == (1, 2)
    assert events[3].log_line == _hook("apply_complete", "a.one")
    assert events[-1].extra == {"outputs": {"bucket": {"value": "b"}}}
    assert run.status == "completed"
    assert run.resources_completed == 1
    assert run.completed_resources == ["a.one"]
    assert "not json" in run.apply_log


@pytest.mark.asyncio
async def test_apply_failure_prefers_diagnostics_over_stderr(session):
    user_id = await _seed(session)
    run = await _apply_run(session, user_id)
    diagnostic = json.dumps({"type": "diagnostic", "diagnostic": {"summary": "s", "detail": "the detail"}})
    process = _process([diagnostic], returncode=1, stderr="noise")

    with _terraform(process):
        events = [e async for e in TerraformExecutor.run_apply(session, run.id, user_id)]

    assert events[-1].event_type == "apply_error"
    assert events[-1].message == "the detail"
    assert run.status == "failed"
    assert run.error_message == "the detail"


@pytest.mark.asyncio
async def test_destroy_classification(session):
    user_id = await _seed(session)
    process = _process(
        [
            "not json",
            json.dumps({"type": "planned_change", "@message": "will destroy a.one"}),
            json.dumps({"type": "planned_change", "@message": "will destroy a.two"}),
            _hook("apply_start", "a.one"),
            _hook("apply_complete", "data.d"),
            _hook("apply_complete", "a.one"),
        ]
    )

    with _terraform(process):
        events = [e async for e in TerraformExecutor.run_destroy(session, user_id, "compute")]

    assert _types(events) == ["progress", "progress", "progress", "resource_complete", "apply_complete"]
    assert events[1].resources_total == 2
    assert events[2].message == "a.one: apply_start"
    assert events[3].message == "Destroyed: a.one"
    assert (events[3].resources_completed, events[3].resources_total) == (1, 2)
    assert events[-1].message == "Destroy complete"


@pytest.mark.asyncio
async def test_destroy_failure_reports_stderr_not_diagnostics(session):
    user_id = await _seed(session)
    diagnostic = json.dumps({"type": "diagnostic", "diagnostic": {"detail": "the detail"}})
    process = _process([diagnostic], returncode=1, stderr="stderr text")

    with _terraform(process):
        events = [e async for e in TerraformExecutor.run_destroy(session, user_id, "compute")]

    assert events[-1].event_type == "apply_error"
    assert events[-1].message == "stderr text"


@pytest.mark.asyncio
async def test_bootstrap_classification(session):
    user_id = await _seed(session, initialized=False)
    process = _process(
        [
            "not json",
            _hook("apply_start", "a.one"),
            _hook("apply_complete", "data.d"),
            _hook("apply_complete", "a.one"),
            json.dumps({"type": "other", "@message": "ignored"}),
        ]
    )

    with _terraform(process, outputs={"state_bucket_name": {"value": "state"}}) as work_dir:
        with patch("app.adapters.registry.get_storage_adapter") as adapter:
            adapter.return_value.build_uri.return_value = "gs://state/"
            events = [e async for e in TerraformExecutor.bootstrap_foundation(session, user_id)]

    messages = [e.message for e in events if e.event_type == "progress"]
    assert messages[:3] == [
        "Starting foundation bootstrap...",
        "Running terraform init...",
        "Running terraform plan...",
    ]
    # Bootstrap counts data sources and ignores other Terraform messages.
    resources = [e for e in events if e.event_type == "resource_complete"]
    assert [e.resource_address for e in resources] == ["data.d", "a.one"]
    assert resources[-1].resources_completed == 2
    assert resources[-1].log_line == ""
    assert events[-1].event_type == "apply_complete"
    assert events[-1].message == "Bootstrap complete. State bucket: state"
    assert not work_dir.exists()


@pytest.mark.asyncio
async def test_bootstrap_failure_marks_run_and_removes_work_dir(session):
    user_id = await _seed(session, initialized=False)
    diagnostic = json.dumps({"type": "diagnostic", "diagnostic": {"detail": "denied"}})

    with _terraform(_process([diagnostic], returncode=1)) as work_dir:
        events = [e async for e in TerraformExecutor.bootstrap_foundation(session, user_id)]

    assert events[-1].event_type == "apply_error"
    assert events[-1].message == "denied"
    run = (await session.execute(text("SELECT status FROM terraform_runs"))).fetchone()
    assert run[0] == "failed"
    assert not work_dir.exists()
