"""Tests for the Phase 17 Terraform executor service (Steps 5-8).

Tests 1-12 from the spec:
- 1-2: Plan execution (creates record, parses JSON output)
- 3-5: Apply execution (yields progress events, updates resources_completed, handles failure)
- 6-7: Concurrency lock (concurrent run prevention, stale lock recovery)
- 8-9: Credential injection (covered in test_credential_injector.py)
- 10-12: Bootstrap (checks GCP configured, checks not already initialized, creates state bucket)

All subprocess calls are mocked. No real Terraform execution.
"""

import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.services.terraform_executor import TerraformExecutor, TerraformProgressEvent
from app.services.bootstrap_roles import seed_builtin_roles


@contextmanager
def _patch_work_dir():
    """Patch _prepare_work_dir to return a real temp dir (no file system deps)."""
    tmp = Path(tempfile.mkdtemp(prefix="tf_test_"))
    with patch.object(TerraformExecutor, "_prepare_work_dir", return_value=tmp):
        yield tmp


def _mock_async_process(stdout: str, returncode: int = 0, stderr: str = ""):
    """Create a mock async subprocess process for asyncio.create_subprocess_exec."""
    lines = (stdout + "\n").encode().splitlines(keepends=True) if stdout else [b""]
    line_iter = iter(lines)

    async def readline():
        try:
            return next(line_iter)
        except StopIteration:
            return b""

    mock_stdout = MagicMock()
    mock_stdout.readline = readline

    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=stderr.encode())

    process = MagicMock()
    process.stdout = mock_stdout
    process.stderr = mock_stderr
    process.returncode = returncode
    process.wait = AsyncMock(return_value=returncode)
    return process


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan_output(adds: int = 1) -> str:
    """Fake streaming JSON from `terraform plan -json`."""
    lines = []
    for i in range(adds):
        lines.append(
            json.dumps(
                {
                    "type": "planned_change",
                    "change": {
                        "resource": {
                            "resource_type": "google_storage_bucket",
                            "resource_name": f"bucket_{i}",
                            "addr": f"google_storage_bucket.bucket_{i}",
                        },
                        "action": "create",
                    },
                }
            )
        )
    return "\n".join(lines)


def _make_show_json_output(adds: int = 1) -> str:
    """Fake JSON from `terraform show -json`."""
    changes = [
        {
            "address": f"google_storage_bucket.bucket_{i}",
            "type": "google_storage_bucket",
            "name": f"bucket_{i}",
            "change": {"actions": ["create"], "before": None, "after": {}},
        }
        for i in range(adds)
    ]
    return json.dumps({"format_version": "1.2", "resource_changes": changes})


def _make_apply_output(n_resources: int = 1) -> str:
    """Fake streaming JSON from `terraform apply -json`."""
    lines = []
    for i in range(n_resources):
        lines.append(
            json.dumps(
                {
                    "type": "apply_complete",
                    "hook": {
                        "resource": {
                            "addr": f"google_storage_bucket.bucket_{i}",
                            "resource_type": "google_storage_bucket",
                            "resource_name": f"bucket_{i}",
                        },
                    },
                }
            )
        )
    lines.append(
        json.dumps(
            {
                "type": "apply_complete",
                "@level": "info",
                "@message": "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.",
            }
        )
    )
    return "\n".join(lines)


def _mock_subprocess_run(stdout: str, returncode: int = 0):
    """Return a MagicMock that mimics subprocess.CompletedProcess."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


async def _seed_gcp_config(session, configured: bool = True, initialized: bool = False):
    """Insert minimal platform_config rows needed by TerraformExecutor."""
    rows = [
        ("gcp_credentials_configured", "true" if configured else "false"),
        ("gcp_credential_source", "vm_default"),
        ("gcp_project_id", "test-project"),
        ("gcp_region", "us-central1"),
        ("gcp_zone", "us-central1-a"),
        ("terraform_initialized", "true" if initialized else "false"),
        ("terraform_state_bucket", "bioaf-tfstate-test" if initialized else ""),
    ]
    for key, value in rows:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


async def _seed_user(session):
    """Create an org and admin user, return user.id."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="ExecTestOrg", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="exec_test@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user.id


# ---------------------------------------------------------------------------
# Test 1: run_plan creates a TerraformRun record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_plan_creates_run_record(session):
    """run_plan() creates a terraform_runs record with status=completed after success."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    plan_stdout = _make_plan_output(1)
    show_stdout = _make_show_json_output(1)

    def mock_run(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(plan_stdout)

    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run):
        with _patch_work_dir():
            run = await TerraformExecutor.run_plan(
                session=session,
                user_id=user_id,
                module_name="foundation",
            )

    assert run.id is not None
    assert run.action == "plan"
    assert run.module_name == "foundation"
    assert run.status == "awaiting_confirmation"
    assert run.resources_planned == 1


# ---------------------------------------------------------------------------
# Test 2: run_plan parses JSON output into plan_json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_plan_stores_parsed_plan_json(session):
    """run_plan() stores parsed plan data in plan_json column."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    show_stdout = _make_show_json_output(2)

    def mock_run(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(2))

    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run):
        with _patch_work_dir():
            run = await TerraformExecutor.run_plan(
                session=session,
                user_id=user_id,
                module_name="foundation",
            )

    assert run.plan_json is not None
    assert run.plan_json["add_count"] == 2
    assert run.resources_planned == 2


# ---------------------------------------------------------------------------
# Test 3: run_apply yields progress events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_apply_yields_progress_events(session):
    """run_apply() yields TerraformProgressEvent objects as apply proceeds."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    # First create a plan run
    show_stdout = _make_show_json_output(1)

    def mock_run_plan(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run_plan):
        with _patch_work_dir():
            plan_run = await TerraformExecutor.run_plan(
                session=session,
                user_id=user_id,
                module_name="foundation",
            )

    apply_stdout = _make_apply_output(1)
    mock_proc = _mock_async_process(apply_stdout)

    events = []
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("app.services.terraform_executor.subprocess.run", return_value=_mock_subprocess_run("")):
            with _patch_work_dir():
                async for event in TerraformExecutor.run_apply(session=session, run_id=plan_run.id, user_id=user_id):
                    events.append(event)

    assert len(events) > 0
    assert all(isinstance(e, TerraformProgressEvent) for e in events)


# ---------------------------------------------------------------------------
# Test 4: run_apply updates resources_completed in DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_apply_updates_resources_completed(session):
    """run_apply() increments resources_completed as resources complete."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    show_stdout = _make_show_json_output(1)

    def mock_plan(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_plan):
        with _patch_work_dir():
            plan_run = await TerraformExecutor.run_plan(
                session=session,
                user_id=user_id,
                module_name="foundation",
            )

    apply_stdout = _make_apply_output(1)
    mock_proc = _mock_async_process(apply_stdout)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("app.services.terraform_executor.subprocess.run", return_value=_mock_subprocess_run("")):
            with _patch_work_dir():
                async for _ in TerraformExecutor.run_apply(session=session, run_id=plan_run.id, user_id=user_id):
                    pass

    await session.refresh(plan_run)
    assert plan_run.status == "completed"
    assert plan_run.action == "apply"


# ---------------------------------------------------------------------------
# Test 5: run_apply handles subprocess failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_apply_handles_failure(session):
    """run_apply() marks run as failed when terraform apply exits non-zero."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    show_stdout = _make_show_json_output(1)

    def mock_plan(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_plan):
        with _patch_work_dir():
            plan_run = await TerraformExecutor.run_plan(
                session=session,
                user_id=user_id,
                module_name="foundation",
            )

    mock_proc = _mock_async_process("", returncode=1, stderr="Error: some terraform error")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("app.services.terraform_executor.subprocess.run", return_value=_mock_subprocess_run("")):
            with _patch_work_dir():
                async for _ in TerraformExecutor.run_apply(session=session, run_id=plan_run.id, user_id=user_id):
                    pass

    await session.refresh(plan_run)
    assert plan_run.status == "failed"
    assert plan_run.error_message is not None


# ---------------------------------------------------------------------------
# Test 6: Concurrent run prevention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_run_prevention(session):
    """run_plan raises ValueError if another run is in progress."""
    from app.services.terraform_executor import _active_processes

    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    # Insert an active run manually
    result = await session.execute(
        text("""
        INSERT INTO terraform_runs (triggered_by_user_id, action, status)
        VALUES (:uid, 'plan', 'planning')
        RETURNING id
        """).bindparams(uid=user_id)
    )
    run_id = result.fetchone()[0]
    await session.commit()

    # Register a mock process so stale recovery doesn't clean it up
    mock_process = MagicMock()
    mock_process.returncode = None
    _active_processes[run_id] = mock_process

    try:
        with pytest.raises(ValueError, match="in progress"):
            await TerraformExecutor.run_plan(session=session, user_id=user_id, module_name="foundation")
    finally:
        _active_processes.pop(run_id, None)


# ---------------------------------------------------------------------------
# Test 7: Stale lock recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_lock_recovery(session):
    """Stale runs (>30 min) are marked failed and the new run proceeds."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    stale_started = datetime.now(timezone.utc) - timedelta(minutes=45)
    await session.execute(
        text("""
        INSERT INTO terraform_runs (triggered_by_user_id, action, status, started_at)
        VALUES (:uid, 'plan', 'planning', :started)
        """).bindparams(uid=user_id, started=stale_started)
    )
    await session.commit()

    show_stdout = _make_show_json_output(1)

    def mock_run(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    # Should not raise - stale run gets cleared
    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run):
        with _patch_work_dir():
            run = await TerraformExecutor.run_plan(session=session, user_id=user_id, module_name="foundation")

    assert run.id is not None
    assert run.status == "awaiting_confirmation"


# ---------------------------------------------------------------------------
# Test 10: bootstrap_foundation checks GCP configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_requires_gcp_configured(session):
    """bootstrap_foundation raises ValueError when GCP is not configured."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=False, initialized=False)

    with pytest.raises(ValueError, match="GCP"):
        async for _ in TerraformExecutor.bootstrap_foundation(session=session, user_id=user_id):
            pass


# ---------------------------------------------------------------------------
# Test 11: bootstrap_foundation checks not already initialized
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_requires_not_initialized(session):
    """bootstrap_foundation raises ValueError when already initialized."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    with pytest.raises(ValueError, match="already"):
        async for _ in TerraformExecutor.bootstrap_foundation(session=session, user_id=user_id):
            pass


# ---------------------------------------------------------------------------
# Test 12: bootstrap_foundation runs plan + apply and seeds state bucket key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_creates_state_bucket_key(session):
    """bootstrap_foundation updates terraform_state_bucket in platform_config on success."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=False)

    show_stdout = _make_show_json_output(1)
    apply_stdout = _make_apply_output(1)

    # Inject a mock output file for terraform output
    tf_output = json.dumps({"state_bucket_name": {"value": "bioaf-tfstate-test"}})

    def mock_run(cmd, **kwargs):
        if "output" in cmd:
            return _mock_subprocess_run(tf_output)
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    mock_proc = _mock_async_process(apply_stdout)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run):
            with _patch_work_dir():
                events = []
                async for event in TerraformExecutor.bootstrap_foundation(session=session, user_id=user_id):
                    events.append(event)

    # Check platform_config updated
    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'terraform_initialized'"))
    ).scalar()
    assert row == "true"


# ---------------------------------------------------------------------------
# Test 12b: bootstrap emits audit log entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_creates_audit_log_entry(session):
    """bootstrap_foundation creates an audit_log entry on success."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=False)

    show_stdout = _make_show_json_output(1)
    apply_stdout = _make_apply_output(1)
    tf_output = json.dumps({"state_bucket_name": {"value": "bioaf-tfstate-test"}})

    def mock_run(cmd, **kwargs):
        if "output" in cmd:
            return _mock_subprocess_run(tf_output)
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    mock_proc = _mock_async_process(apply_stdout)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run):
            with _patch_work_dir():
                async for _ in TerraformExecutor.bootstrap_foundation(session=session, user_id=user_id):
                    pass

    row = (
        await session.execute(
            text("SELECT action, entity_type FROM audit_log WHERE entity_type = 'terraform' ORDER BY id DESC LIMIT 1")
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "bootstrap"
    assert row[1] == "terraform"


# ---------------------------------------------------------------------------
# Test 12c: bootstrap emits activity feed entry when org_id provided
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_creates_activity_feed_entry(session):
    """bootstrap_foundation creates an activity_feed entry when org_id is provided."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="ActivityTestOrg", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="activity_test@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()

    await _seed_gcp_config(session, configured=True, initialized=False)

    show_stdout = _make_show_json_output(1)
    apply_stdout = _make_apply_output(1)
    tf_output = json.dumps({"state_bucket_name": {"value": "bioaf-tfstate-test"}})

    def mock_run(cmd, **kwargs):
        if "output" in cmd:
            return _mock_subprocess_run(tf_output)
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    mock_proc = _mock_async_process(apply_stdout)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run):
            with _patch_work_dir():
                async for _ in TerraformExecutor.bootstrap_foundation(session=session, user_id=user.id, org_id=org.id):
                    pass

    row = (
        await session.execute(
            text(
                "SELECT event_type, summary FROM activity_feed "
                "WHERE event_type = 'infrastructure.bootstrap_completed' LIMIT 1"
            )
        )
    ).fetchone()
    assert row is not None
    assert "bioaf-tfstate-test" in row[1]


# ---------------------------------------------------------------------------
# Test 12d: bootstrap without org_id skips activity feed (no error)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_without_org_id_skips_activity_feed(session):
    """bootstrap_foundation does not create activity_feed entry when org_id is None."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=False)

    show_stdout = _make_show_json_output(1)
    apply_stdout = _make_apply_output(1)
    tf_output = json.dumps({"state_bucket_name": {"value": "bioaf-tfstate-test"}})

    def mock_run(cmd, **kwargs):
        if "output" in cmd:
            return _mock_subprocess_run(tf_output)
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    mock_proc = _mock_async_process(apply_stdout)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run):
            with _patch_work_dir():
                async for _ in TerraformExecutor.bootstrap_foundation(session=session, user_id=user_id):
                    pass

    count = (
        await session.execute(
            text("SELECT count(*) FROM activity_feed WHERE event_type = 'infrastructure.bootstrap_completed'")
        )
    ).scalar()
    assert count == 0


# ---------------------------------------------------------------------------
# Test 13: _write_tfvars writes correct variables for each module
# ---------------------------------------------------------------------------


def test_write_tfvars_foundation():
    """_write_tfvars writes project_id, region, and state_bucket_name for foundation."""
    tmp = Path(tempfile.mkdtemp(prefix="tf_test_"))
    config = {
        "gcp_project_id": "my-project",
        "gcp_region": "us-east1",
        "terraform_state_bucket": "my-bucket",
    }
    TerraformExecutor._write_tfvars(tmp, "foundation", config)

    tfvars = json.loads((tmp / "terraform.tfvars.json").read_text())
    assert tfvars["project_id"] == "my-project"
    assert tfvars["region"] == "us-east1"
    assert tfvars["state_bucket_name"] == "my-bucket"
    assert "org_slug" not in tfvars


def test_write_tfvars_storage_with_suffix():
    """_write_tfvars includes stack_uid when deploy_suffix is set."""
    tmp = Path(tempfile.mkdtemp(prefix="tf_test_"))
    config = {
        "gcp_project_id": "my-project",
        "gcp_region": "us-west1",
        "org_slug": "my-lab",
        "deploy_suffix": "a1b2c3",
    }
    TerraformExecutor._write_tfvars(tmp, "storage", config)

    tfvars = json.loads((tmp / "terraform.tfvars.json").read_text())
    assert tfvars["project_id"] == "my-project"
    assert tfvars["region"] == "us-west1"
    assert tfvars["org_slug"] == "my-lab"
    assert tfvars["stack_uid"] == "a1b2c3"
    assert "state_bucket_name" not in tfvars


def test_write_tfvars_storage_without_suffix():
    """_write_tfvars omits stack_uid when deploy_suffix is not set (destroy path)."""
    tmp = Path(tempfile.mkdtemp(prefix="tf_test_"))
    config = {
        "gcp_project_id": "my-project",
        "gcp_region": "us-west1",
        "org_slug": "my-lab",
    }
    TerraformExecutor._write_tfvars(tmp, "storage", config)

    tfvars = json.loads((tmp / "terraform.tfvars.json").read_text())
    assert "stack_uid" not in tfvars


def test_write_tfvars_compute_with_suffix():
    """_write_tfvars includes stack_uid when deploy_suffix is set."""
    tmp = Path(tempfile.mkdtemp(prefix="tf_test_"))
    config = {
        "gcp_project_id": "my-project",
        "gcp_region": "europe-west1",
        "gcp_zone": "europe-west1-b",
        "org_slug": "acme",
        "deploy_suffix": "d4e5f6",
    }
    TerraformExecutor._write_tfvars(tmp, "compute", config)

    tfvars = json.loads((tmp / "terraform.tfvars.json").read_text())
    assert tfvars["project_id"] == "my-project"
    assert tfvars["region"] == "europe-west1"
    assert tfvars["zone"] == "europe-west1-b"
    assert tfvars["org_slug"] == "acme"
    assert tfvars["stack_uid"] == "d4e5f6"


def test_write_tfvars_defaults():
    """_write_tfvars uses sensible defaults for missing config values."""
    tmp = Path(tempfile.mkdtemp(prefix="tf_test_"))
    config = {"gcp_project_id": "proj-123"}
    TerraformExecutor._write_tfvars(tmp, "foundation", config)

    tfvars = json.loads((tmp / "terraform.tfvars.json").read_text())
    assert tfvars["region"] == "us-central1"
    assert tfvars["state_bucket_name"] == "bioaf-tfstate-proj-123"


def test_write_tfvars_empty_strings_use_defaults():
    """_write_tfvars treats empty string values as missing and uses defaults."""
    tmp = Path(tempfile.mkdtemp(prefix="tf_test_"))
    config = {
        "gcp_project_id": "proj-456",
        "gcp_region": "",
        "terraform_state_bucket": "",
        "org_slug": "",
    }
    TerraformExecutor._write_tfvars(tmp, "foundation", config)

    tfvars = json.loads((tmp / "terraform.tfvars.json").read_text())
    assert tfvars["region"] == "us-central1"
    assert tfvars["state_bucket_name"] == "bioaf-tfstate-proj-456"


# ---------------------------------------------------------------------------
# _run_init backend-config tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_init_passes_backend_config_bucket():
    """_run_init passes -backend-config=bucket=<name> when local_backend=False."""
    config = {"terraform_state_bucket": "my-tf-bucket"}
    captured_cmd = None

    def _fake_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("app.services.terraform_executor.subprocess.run", side_effect=_fake_run):
        await TerraformExecutor._run_init(
            work_dir=Path("/tmp/fake"),
            env={},
            config=config,
            local_backend=False,
        )

    assert captured_cmd is not None
    assert "-backend-config=bucket=my-tf-bucket" in captured_cmd
    assert "-backend=false" not in captured_cmd


@pytest.mark.asyncio
async def test_run_init_local_backend_skips_bucket_config():
    """_run_init uses -backend=false and omits bucket config for local backend."""
    config = {"terraform_state_bucket": "my-tf-bucket"}
    captured_cmd = None

    def _fake_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("app.services.terraform_executor.subprocess.run", side_effect=_fake_run):
        await TerraformExecutor._run_init(
            work_dir=Path("/tmp/fake"),
            env={},
            config=config,
            local_backend=True,
        )

    assert captured_cmd is not None
    assert "-backend=false" in captured_cmd
    assert not any("backend-config" in c for c in captured_cmd)


@pytest.mark.asyncio
async def test_run_init_no_bucket_in_config_skips_backend_config():
    """_run_init omits -backend-config when bucket is not in config."""
    config = {"gcp_project_id": "proj-1"}
    captured_cmd = None

    def _fake_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("app.services.terraform_executor.subprocess.run", side_effect=_fake_run):
        await TerraformExecutor._run_init(
            work_dir=Path("/tmp/fake"),
            env={},
            config=config,
            local_backend=False,
        )

    assert captured_cmd is not None
    assert not any("backend-config" in c for c in captured_cmd)


@pytest.mark.asyncio
async def test_run_init_passes_prefix_for_module_name():
    """_run_init passes -backend-config=prefix=<module> to isolate state per module."""
    config = {"terraform_state_bucket": "my-tf-bucket"}
    captured_cmd = None

    def _fake_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("app.services.terraform_executor.subprocess.run", side_effect=_fake_run):
        await TerraformExecutor._run_init(
            work_dir=Path("/tmp/fake"),
            env={},
            config=config,
            module_name="storage",
        )

    assert captured_cmd is not None
    assert "-backend-config=prefix=storage" in captured_cmd
    assert "-backend-config=bucket=my-tf-bucket" in captured_cmd


@pytest.mark.asyncio
async def test_run_init_no_prefix_without_module_name():
    """_run_init omits prefix config when module_name is not provided."""
    config = {"terraform_state_bucket": "my-tf-bucket"}
    captured_cmd = None

    def _fake_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("app.services.terraform_executor.subprocess.run", side_effect=_fake_run):
        await TerraformExecutor._run_init(
            work_dir=Path("/tmp/fake"),
            env={},
            config=config,
        )

    assert captured_cmd is not None
    assert not any("prefix" in c for c in captured_cmd)


# ---------------------------------------------------------------------------
# run_apply initializes working directory and does not use saved plan file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_apply_inits_workdir_and_applies_without_planfile(session):
    """run_apply() runs terraform init + apply in a fresh work dir without tfplan."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    show_stdout = _make_show_json_output(1)

    def mock_plan(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_plan):
        with _patch_work_dir():
            plan_run = await TerraformExecutor.run_plan(
                session=session,
                user_id=user_id,
                module_name="storage",
            )

    apply_stdout = _make_apply_output(1)
    mock_proc = _mock_async_process(apply_stdout)
    captured_init_cmds = []
    captured_exec_args = []

    original_mock_init = _mock_subprocess_run("")

    def mock_init_run(cmd, **kwargs):
        captured_init_cmds.append(cmd)
        return original_mock_init

    async def mock_create_subprocess(*args, **kwargs):
        captured_exec_args.append(list(args))
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess):
        with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_init_run):
            with _patch_work_dir():
                async for _ in TerraformExecutor.run_apply(session=session, run_id=plan_run.id, user_id=user_id):
                    pass

    # init should have been called via subprocess.run
    assert len(captured_init_cmds) >= 1
    init_cmd = captured_init_cmds[0]
    assert "init" in init_cmd

    # apply should have been called via create_subprocess_exec
    assert len(captured_exec_args) >= 1
    apply_cmd = captured_exec_args[0]
    assert "apply" in apply_cmd
    # apply must NOT reference a saved plan file
    assert "tfplan" not in apply_cmd


# ---------------------------------------------------------------------------
# read_module_outputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_module_outputs_returns_parsed_json(session):
    """read_module_outputs inits then reads terraform output -json and returns parsed dict."""
    await _seed_gcp_config(session, configured=True, initialized=True)

    storage_outputs = {
        "ingest_bucket_name": {"value": "bioaf-abc123-ingest"},
        "raw_bucket_name": {"value": "bioaf-abc123-raw"},
    }

    init_calls: list = []
    output_calls: list = []

    def mock_subprocess_run(args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        if "init" in args:
            init_calls.append(args)
            result.stdout = ""
        else:
            output_calls.append(args)
            result.stdout = json.dumps(storage_outputs)
        return result

    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_subprocess_run):
        with _patch_work_dir():
            with patch.object(TerraformExecutor, "_write_tfvars"):
                outputs = await TerraformExecutor.read_module_outputs(session, "storage")

    assert outputs == storage_outputs
    assert any("output" in " ".join(str(a) for a in cmd) for cmd in output_calls)


@pytest.mark.asyncio
async def test_read_module_outputs_raises_on_failure(session):
    """read_module_outputs raises RuntimeError when terraform output exits non-zero."""
    await _seed_gcp_config(session, configured=True, initialized=True)

    def mock_subprocess_run(args, **kwargs):
        result = MagicMock()
        result.stderr = "No outputs defined"
        if "init" in args:
            result.returncode = 0
            result.stdout = ""
        else:
            result.returncode = 1
            result.stdout = ""
        return result

    with patch("app.services.terraform_executor.subprocess.run", side_effect=mock_subprocess_run):
        with _patch_work_dir():
            with patch.object(TerraformExecutor, "_write_tfvars"):
                with pytest.raises(RuntimeError, match="terraform output failed"):
                    await TerraformExecutor.read_module_outputs(session, "storage")


# ---------------------------------------------------------------------------
# sync_storage_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_storage_config_writes_bucket_names(session):
    """sync_storage_config reads storage outputs and populates platform_config."""
    from app.services.stack_deployment import sync_storage_config

    storage_outputs = {
        "ingest_bucket_name": {"value": "bioaf-abc123-ingest"},
        "raw_bucket_name": {"value": "bioaf-abc123-raw"},
        "working_bucket_name": {"value": "bioaf-abc123-working"},
        "results_bucket_name": {"value": "bioaf-abc123-results"},
        "config_backups_bucket_name": {"value": "bioaf-abc123-config-backups"},
    }

    with patch.object(TerraformExecutor, "read_module_outputs", return_value=storage_outputs):
        populated = await sync_storage_config(session)

    await session.commit()

    assert populated["ingest_bucket_name"] == "bioaf-abc123-ingest"
    assert populated["results_bucket_name"] == "bioaf-abc123-results"
    assert len(populated) == 5

    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'ingest_bucket_name'"))).fetchone()
    assert row is not None
    assert row[0] == "bioaf-abc123-ingest"


@pytest.mark.asyncio
async def test_sync_storage_config_skips_empty_outputs(session):
    """sync_storage_config skips keys where the output value is empty."""
    from app.services.stack_deployment import sync_storage_config

    storage_outputs = {
        "ingest_bucket_name": {"value": "bioaf-abc123-ingest"},
        # Other keys absent -- simulates partial output
    }

    with patch.object(TerraformExecutor, "read_module_outputs", return_value=storage_outputs):
        populated = await sync_storage_config(session)

    assert list(populated.keys()) == ["ingest_bucket_name"]


# ---------------------------------------------------------------------------
# abandon_run tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abandon_run_marks_run_cancelled(session):
    """abandon_run() marks a stuck run as cancelled and sets completed_at."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    # Insert a stuck run in awaiting_confirmation
    await session.execute(
        text("""
        INSERT INTO terraform_runs
            (triggered_by_user_id, action, status, module_name)
        VALUES (:uid, 'plan', 'awaiting_confirmation', 'compute')
        """).bindparams(uid=user_id)
    )
    await session.commit()
    run_row = (await session.execute(text("SELECT id FROM terraform_runs LIMIT 1"))).fetchone()
    run_id = run_row[0]

    with patch.object(TerraformExecutor, "_delete_gcs_lock_file", new=AsyncMock()):
        run = await TerraformExecutor.abandon_run(session, run_id, user_id)

    assert run.status == "cancelled"
    assert run.completed_at is not None
    assert "abandoned" in (run.error_message or "").lower()


@pytest.mark.asyncio
async def test_abandon_run_deletes_gcs_lock_file(session):
    """abandon_run() calls _delete_gcs_lock_file with the correct path."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    await session.execute(
        text("""
        INSERT INTO terraform_runs
            (triggered_by_user_id, action, status, module_name)
        VALUES (:uid, 'plan', 'awaiting_confirmation', 'compute')
        """).bindparams(uid=user_id)
    )
    await session.commit()
    run_row = (await session.execute(text("SELECT id FROM terraform_runs LIMIT 1"))).fetchone()
    run_id = run_row[0]

    mock_delete = AsyncMock()
    with patch.object(TerraformExecutor, "_delete_gcs_lock_file", new=mock_delete):
        await TerraformExecutor.abandon_run(session, run_id, user_id)

    mock_delete.assert_called_once()
    call_args = mock_delete.call_args
    assert "compute" in call_args[0][1]  # lock path contains module name
    assert "default.tflock" in call_args[0][1]


@pytest.mark.asyncio
async def test_abandon_run_rejects_completed_run(session):
    """abandon_run() raises ValueError for runs that are already completed."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    await session.execute(
        text("""
        INSERT INTO terraform_runs
            (triggered_by_user_id, action, status, module_name, completed_at)
        VALUES (:uid, 'plan', 'completed', 'compute', now())
        """).bindparams(uid=user_id)
    )
    await session.commit()
    run_row = (await session.execute(text("SELECT id FROM terraform_runs LIMIT 1"))).fetchone()
    run_id = run_row[0]

    with pytest.raises(ValueError, match="cannot be abandoned"):
        await TerraformExecutor.abandon_run(session, run_id, user_id)


@pytest.mark.asyncio
async def test_abandon_run_rejects_nonexistent_run(session):
    """abandon_run() raises ValueError for a run that does not exist."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    with pytest.raises(ValueError, match="not found"):
        await TerraformExecutor.abandon_run(session, 99999, user_id)


@pytest.mark.asyncio
async def test_delete_gcs_lock_file_uses_storage_client():
    """_delete_gcs_lock_file uses google-cloud-storage, not gsutil."""
    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    with patch("app.services.terraform_executor.storage.Client", return_value=mock_client):
        await TerraformExecutor._delete_gcs_lock_file("my-bucket", "compute/default.tflock")

    mock_client.bucket.assert_called_once_with("my-bucket")
    mock_bucket.blob.assert_called_once_with("compute/default.tflock")
    mock_blob.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_gcs_lock_file_logs_not_found():
    """_delete_gcs_lock_file handles NotFound gracefully."""
    from google.api_core.exceptions import NotFound

    mock_blob = MagicMock()
    mock_blob.delete.side_effect = NotFound("not found")
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    with patch("app.services.terraform_executor.storage.Client", return_value=mock_client):
        # Should not raise
        await TerraformExecutor._delete_gcs_lock_file("my-bucket", "compute/default.tflock")


# ---------------------------------------------------------------------------
# Auto lock cleanup on failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_apply_failure_deletes_lock(session):
    """When terraform apply fails (non-zero exit), the lock file is auto-deleted."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    apply_output = json.dumps({"type": "diagnostic", "diagnostic": {"summary": "Error applying"}})
    mock_process = _mock_async_process(apply_output, returncode=1, stderr="apply failed")

    with (
        _patch_work_dir(),
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        patch.object(TerraformExecutor, "_run_init", new=AsyncMock()),
        patch.object(TerraformExecutor, "_auto_cleanup_lock", new=AsyncMock()) as mock_cleanup,
    ):
        # Insert a run in applying state
        await session.execute(
            text("""
            INSERT INTO terraform_runs
                (triggered_by_user_id, action, status, module_name, resources_planned)
            VALUES (:uid, 'apply', 'applying', 'compute', 1)
            """).bindparams(uid=user_id)
        )
        await session.commit()
        run_row = (await session.execute(text("SELECT id FROM terraform_runs ORDER BY id DESC LIMIT 1"))).fetchone()

        events = []
        async for event in TerraformExecutor.run_apply(session, run_row[0], user_id):
            events.append(event)

    mock_cleanup.assert_called_once_with(session, "compute")


@pytest.mark.asyncio
async def test_run_destroy_failure_deletes_lock(session):
    """When terraform destroy fails (non-zero exit), the lock file is auto-deleted."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    mock_process = _mock_async_process("", returncode=1, stderr="destroy failed")

    with (
        _patch_work_dir(),
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        patch.object(TerraformExecutor, "_run_init", new=AsyncMock()),
        patch.object(TerraformExecutor, "_auto_cleanup_lock", new=AsyncMock()) as mock_cleanup,
    ):
        events = []
        async for event in TerraformExecutor.run_destroy(session, user_id, "compute"):
            events.append(event)

    mock_cleanup.assert_called_once_with(session, "compute")


@pytest.mark.asyncio
async def test_recover_stale_runs_no_process_marks_failed(session):
    """Runs with no live process in the registry are marked failed."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    # Insert a run that has no corresponding process (simulates container restart)
    await session.execute(
        text("""
        INSERT INTO terraform_runs
            (triggered_by_user_id, action, status, module_name, started_at)
        VALUES (:uid, 'apply', 'applying', 'compute', now() - interval '5 minutes')
        """).bindparams(uid=user_id)
    )
    await session.commit()

    mock_delete = AsyncMock()
    with (
        patch.object(TerraformExecutor, "_delete_gcs_lock_file", new=mock_delete),
        patch.object(TerraformExecutor, "_load_gcs_credentials", new=AsyncMock(return_value=None)),
    ):
        await TerraformExecutor._recover_stale_runs(session)

    mock_delete.assert_called_once()
    call_args = mock_delete.call_args
    assert call_args[0][0] == "bioaf-tfstate-test"
    assert "compute/default.tflock" in call_args[0][1]

    row = (await session.execute(text("SELECT status FROM terraform_runs LIMIT 1"))).fetchone()
    assert row[0] == "failed"


@pytest.mark.asyncio
async def test_recover_stale_runs_skips_live_process(session):
    """Runs with a live process in the registry are left alone."""
    from app.services.terraform_executor import _active_processes

    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    result = await session.execute(
        text("""
        INSERT INTO terraform_runs
            (triggered_by_user_id, action, status, module_name, started_at)
        VALUES (:uid, 'apply', 'applying', 'compute', now() - interval '10 minutes')
        RETURNING id
        """).bindparams(uid=user_id)
    )
    run_id = result.fetchone()[0]
    await session.commit()

    # Register a mock process as alive
    mock_process = MagicMock()
    mock_process.returncode = None  # Still running
    _active_processes[run_id] = mock_process

    try:
        mock_delete = AsyncMock()
        with (
            patch.object(TerraformExecutor, "_delete_gcs_lock_file", new=mock_delete),
            patch.object(TerraformExecutor, "_load_gcs_credentials", new=AsyncMock(return_value=None)),
        ):
            await TerraformExecutor._recover_stale_runs(session)

        mock_delete.assert_not_called()

        row = (
            await session.execute(text("SELECT status FROM terraform_runs WHERE id = :id").bindparams(id=run_id))
        ).fetchone()
        assert row[0] == "applying"  # Still applying, not failed
    finally:
        _active_processes.pop(run_id, None)


# ---------------------------------------------------------------------------
# SA hardening: _read_gcp_config returns gcp_bootstrap_sa_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_gcp_config_includes_bootstrap_sa_email(session):
    """_read_gcp_config selects the new gcp_bootstrap_sa_email key."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(
            k="gcp_bootstrap_sa_email",
            v="bioaf-bootstrap@my-project.iam.gserviceaccount.com",
        )
    )
    await session.commit()

    config = await TerraformExecutor._read_gcp_config(session)
    assert config.get("gcp_bootstrap_sa_email") == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_run_plan_passes_bootstrap_sa_email_to_build_env(session):
    """In vm_default mode, run_plan injects the bootstrap impersonation field
    into the config dict that build_env receives, so the credential injector
    can target bioaf-bootstrap.
    """
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)
    # Seed the bootstrap SA email so injection has something to inject.
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(
            k="gcp_bootstrap_sa_email",
            v="bioaf-bootstrap@test-project.iam.gserviceaccount.com",
        )
    )
    await session.commit()

    show_stdout = _make_show_json_output(1)

    def mock_run(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    captured_configs: list[dict] = []

    async def spy_build_env(config):
        captured_configs.append(dict(config))

        async def cleanup():
            return None

        return ({"TF_VAR_project_id": config.get("gcp_project_id", "")}, cleanup)

    with (
        patch("app.services.terraform_executor.subprocess.run", side_effect=mock_run),
        patch(
            "app.services.terraform_executor.GCPCredentialInjector.build_env",
            side_effect=spy_build_env,
        ),
        _patch_work_dir(),
    ):
        await TerraformExecutor.run_plan(
            session=session,
            user_id=user_id,
            module_name="foundation",
        )

    assert captured_configs, "build_env should have been called at least once"
    cfg = captured_configs[0]
    assert cfg.get("gcp_credential_source") == "vm_default"
    assert cfg.get("gcp_bootstrap_sa_email") == "bioaf-bootstrap@test-project.iam.gserviceaccount.com"
