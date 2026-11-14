import asyncio as asyncio_mod
import logging
import stat
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_backup_status(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/backups/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "tiers" in data
    assert "overall_status" in data
    assert len(data["tiers"]) == 4
    tier_names = {t["tier"] for t in data["tiers"]}
    assert tier_names == {"postgres", "gcs", "platform_config", "terraform_state"}


@pytest.mark.asyncio
async def test_get_backup_status_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/backups/status",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_config_snapshots(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/backups/config-snapshots",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "snapshots" in data
    assert "total" in data
    assert "page" in data


@pytest.mark.asyncio
async def test_get_config_snapshot_diff(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/backups/config-snapshots/2025-01-01/diff",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "snapshot_date" in data
    assert "compare_to" in data


@pytest.mark.asyncio
async def test_restore_config(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/backups/restore/config",
        json={"confirmation_token": "CONFIRM"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "initiated"


@pytest.mark.asyncio
async def test_update_backup_settings(client: AsyncClient, admin_token: str):
    response = await client.put(
        "/api/backups/settings",
        json={"postgres_retention_days": 30},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "updated"


@pytest.mark.asyncio
async def test_update_backup_settings_enforces_postgres_minimum(client: AsyncClient, admin_token: str):
    response = await client.put(
        "/api/backups/settings",
        json={"postgres_retention_days": 0},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_trigger_postgres_backup_via_api(client: AsyncClient, admin_token: str):
    """Trigger endpoint calls run_postgres_backup and returns result."""
    with patch(
        "app.api.backups.BackupService.run_postgres_backup",
        new_callable=AsyncMock,
        return_value={
            "status": "completed",
            "filename": "pgdump-test.dump",
            "size_bytes": 1024,
            "duration_seconds": 1.5,
        },
    ):
        response = await client.post(
            "/api/backups/trigger/postgres",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_postgres_snapshots_empty(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/backups/postgres-snapshots",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["snapshots"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_trigger_config_backup_via_api(client: AsyncClient, admin_token: str):
    """Trigger config backup endpoint calls run_config_backup."""
    with patch(
        "app.api.backups.BackupService.run_config_backup",
        new_callable=AsyncMock,
        return_value={
            "status": "completed",
            "filename": "config-test.json",
            "size_bytes": 512,
        },
    ):
        response = await client.post(
            "/api/backups/trigger/config",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_get_backup_settings(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/backups/settings",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "postgres_retention_days" in data
    assert "postgres_schedule_hours" in data
    assert "config_retention_days" in data
    assert "config_schedule_hours" in data
    # New schedule fields
    assert "postgres_schedule_enabled" in data
    assert "config_schedule_enabled" in data
    assert "postgres_next_run" in data
    assert "config_next_run" in data
    # Defaults to disabled
    assert data["postgres_schedule_enabled"] is False
    assert data["config_schedule_enabled"] is False


@pytest.mark.asyncio
async def test_update_backup_settings_returns_updated_values(client: AsyncClient, admin_token: str):
    """Settings update returns the persisted values."""
    response = await client.put(
        "/api/backups/settings",
        json={"postgres_retention_days": 21, "config_schedule_hours": 12},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["settings"]["postgres_retention_days"] == 21
    assert data["settings"]["config_schedule_hours"] == 12


@pytest.mark.asyncio
async def test_enable_postgres_schedule_with_first_run_now(client: AsyncClient, admin_token: str):
    """Enabling schedule with first_run='now' sets next_run to approximately now."""
    response = await client.put(
        "/api/backups/settings",
        json={
            "postgres_schedule_enabled": True,
            "postgres_first_run": "now",
            "postgres_schedule_hours": 24,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()["settings"]
    assert data["postgres_schedule_enabled"] is True
    assert data["postgres_next_run"] is not None
    # next_run should be within the last 60 seconds (approximately now)
    next_run = datetime.fromisoformat(data["postgres_next_run"])
    now = datetime.now(timezone.utc)
    assert abs((now - next_run).total_seconds()) < 60


@pytest.mark.asyncio
async def test_enable_postgres_schedule_with_future_time(client: AsyncClient, admin_token: str):
    """Enabling schedule with a future ISO datetime sets next_run to that time."""
    future = datetime.now(timezone.utc) + timedelta(hours=6)
    future_iso = future.isoformat()
    response = await client.put(
        "/api/backups/settings",
        json={
            "postgres_schedule_enabled": True,
            "postgres_first_run": future_iso,
            "postgres_schedule_hours": 12,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()["settings"]
    assert data["postgres_schedule_enabled"] is True
    next_run = datetime.fromisoformat(data["postgres_next_run"])
    assert abs((next_run - future).total_seconds()) < 2


@pytest.mark.asyncio
async def test_disable_postgres_schedule(client: AsyncClient, admin_token: str):
    """Disabling schedule clears next_run."""
    # Enable first
    await client.put(
        "/api/backups/settings",
        json={"postgres_schedule_enabled": True, "postgres_first_run": "now"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Disable
    response = await client.put(
        "/api/backups/settings",
        json={"postgres_schedule_enabled": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()["settings"]
    assert data["postgres_schedule_enabled"] is False
    assert data["postgres_next_run"] is None


@pytest.mark.asyncio
async def test_advance_next_run_after_backup(session):
    """After a backup completes, next_run advances by cadence hours."""
    from app.services.backup_service import BackupService

    # Set up schedule: enabled, next_run = now, cadence = 12h
    now = datetime.now(timezone.utc)
    await BackupService.update_backup_settings(
        session,
        {
            "postgres_schedule_enabled": True,
            "postgres_first_run": "now",
            "postgres_schedule_hours": 12,
        },
    )
    await session.commit()

    # Advance next_run
    await BackupService.advance_next_run(session, "postgres")
    await session.commit()

    settings_after = await BackupService.get_backup_settings(session)
    next_run = datetime.fromisoformat(settings_after["postgres_next_run"])
    expected = now + timedelta(hours=12)
    assert abs((next_run - expected).total_seconds()) < 5


@pytest.mark.asyncio
async def test_list_tfstate_files(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/backups/tfstate-files",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert "files" in response.json()


@pytest.mark.asyncio
async def test_download_tfstate_not_found(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/backups/tfstate-download/nonexistent.tfstate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_backup_health_check(admin_user, session):
    """Test backup health check does not error."""
    from app.services.backup_service import BackupService

    await BackupService.check_backup_health(session, admin_user.organization_id)


@pytest.mark.asyncio
async def test_trigger_postgres_backup_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.post(
        "/api/backups/trigger/postgres",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


# --- Database Restore tests ---


@pytest.mark.asyncio
async def test_restore_status_inactive(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/backups/restore/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["active"] is False


@pytest.mark.asyncio
async def test_accept_restore_when_not_active(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/backups/restore/accept",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reject_restore_when_not_active(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/backups/restore/reject",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_start_restore_calls_service(client: AsyncClient, admin_token: str):
    """Start restore endpoint calls RestoreService.start."""
    with patch(
        "app.api.backups.RestoreService.start",
        new_callable=AsyncMock,
        return_value={"status": "reviewing", "message": "Restore active"},
    ):
        response = await client.post(
            "/api/backups/restore/postgres",
            json={"filename": "pgdump-20260403-030000.dump"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "reviewing"


@pytest.mark.asyncio
async def test_start_restore_conflict_when_active(client: AsyncClient, admin_token: str):
    """Returns 409 if a restore is already in progress."""
    with patch(
        "app.api.backups.RestoreService.start",
        new_callable=AsyncMock,
        return_value={"status": "error", "message": "A restore review is already active"},
    ):
        response = await client.post(
            "/api/backups/restore/postgres",
            json={"filename": "pgdump-test.dump"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_restore_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.post(
        "/api/backups/restore/postgres",
        json={"filename": "pgdump-test.dump"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


# --- RestoreService.start filename validation tests ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_filename",
    [
        "../etc/passwd",
        "pgdump-20260403-030000.dump/../etc/passwd",
        "subdir/pgdump-20260403-030000.dump",
        "pgdump-20260403-030000.tar",
        "pgdump-bad.dump",
        "",
        "pgdump-20260403-030000.dump\x00.evil",
    ],
)
async def test_restore_start_rejects_unsafe_filename(session, bad_filename):
    """Filenames that don't match the pgdump pattern are rejected before any I/O."""
    from app.services import backup_service
    from app.services.backup_service import RestoreService

    backup_service._restore_state["active"] = False

    with patch("app.services.backup_service._get_backups_bucket", new_callable=AsyncMock) as mock_bucket:
        result = await RestoreService.start(session, org_id=1, filename=bad_filename)

    assert result["status"] == "error"
    assert "filename" in result["message"].lower()
    mock_bucket.assert_not_called()


@pytest.mark.asyncio
async def test_restore_start_accepts_valid_pgdump_filename(session):
    """A real pgdump-<timestamp>.dump filename passes validation and proceeds past it."""
    from app.services import backup_service
    from app.services.backup_service import RestoreService

    backup_service._restore_state["active"] = False

    with patch(
        "app.services.backup_service._get_backups_bucket",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await RestoreService.start(session, org_id=1, filename="pgdump-20260403-030000.dump")

    # Validation passed and execution reached the bucket check, which we stubbed
    # to return None so the call exits with the bucket-not-configured error.
    assert result["status"] == "error"
    assert "bucket" in result["message"].lower()


# --- _build_restore_url tests ---


def test_build_restore_url_only_replaces_database_name():
    """_build_restore_url must change only the database path, not the username."""
    from app.services.backup_service import _build_restore_url

    # Production-style URL where username matches database name
    with patch("app.services.backup_service.settings") as mock_settings:
        mock_settings.database_url = "postgresql+asyncpg://bioaf:secretpass@db:5432/bioaf"
        result = _build_restore_url()

    assert result == "postgresql+asyncpg://bioaf:secretpass@db:5432/bioaf_restore"


def test_build_restore_url_preserves_dev_username():
    """_build_restore_url must not mangle usernames containing 'bioaf'."""
    from app.services.backup_service import _build_restore_url

    with patch("app.services.backup_service.settings") as mock_settings:
        mock_settings.database_url = "postgresql+asyncpg://bioaf_app:devpassword@postgres:5432/bioaf"
        result = _build_restore_url()

    assert result == "postgresql+asyncpg://bioaf_app:devpassword@postgres:5432/bioaf_restore"


def test_build_restore_url_preserves_password_containing_bioaf():
    """_build_restore_url must not mangle passwords that happen to contain 'bioaf'."""
    from app.services.backup_service import _build_restore_url

    with patch("app.services.backup_service.settings") as mock_settings:
        mock_settings.database_url = "postgresql+asyncpg://user:bioaf_pass@db:5432/bioaf"
        result = _build_restore_url()

    assert result == "postgresql+asyncpg://user:bioaf_pass@db:5432/bioaf_restore"


# --- GCS backup status tests ---


def _mock_status_adapter(object_names: list[str], *, size: int = 1024, versioning: bool = True):
    """Build a storage-adapter mock for BackupService._gcs_status (Phase 3).

    ``object_names`` are bucket-relative keys (e.g. "postgres/pgdump-...dump");
    they are returned for every list_objects prefix and the service's regex
    filters per tier, matching the old list_blobs behavior.
    """
    from app.adapters.models import StoredObject

    objs = [
        StoredObject(filename=name.split("/")[-1], storage_uri=f"gs://test-bucket/{name}", size_bytes=size)
        for name in object_names
    ]
    adapter = AsyncMock()
    adapter.build_uri = MagicMock(side_effect=lambda bucket, key: f"gs://{bucket}/{key.lstrip('/')}")
    adapter.list_objects.return_value = objs
    adapter.get_bucket_info.return_value = {"versioning_enabled": versioning}
    return adapter


@pytest.mark.asyncio
async def test_gcs_status_with_recent_postgres_backup():
    """With a recent pg_dump object in storage, postgres tier shows healthy."""
    from app.services.backup_service import BackupService

    now = datetime.now(timezone.utc)
    recent_name = f"postgres/pgdump-{now.strftime('%Y%m%d-%H%M%S')}.dump"
    adapter = _mock_status_adapter([recent_name], size=50000)

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.backup_service.settings") as mock_settings,
    ):
        mock_settings.backup_postgres_interval_hours = 24
        mock_settings.backup_postgres_retention_days = 14
        mock_settings.backup_config_retention_days = 30

        tiers = await BackupService._gcs_status("test-bucket")

    postgres = next(t for t in tiers if t["tier"] == "postgres")
    assert postgres["status"] == "healthy"
    assert postgres["backup_count"] == 1
    assert postgres["size_bytes"] == 50000


@pytest.mark.asyncio
async def test_gcs_status_no_backups():
    """With no objects, postgres tier shows unknown."""
    from app.services.backup_service import BackupService

    adapter = _mock_status_adapter([])

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.backup_service.settings") as mock_settings,
    ):
        mock_settings.backup_postgres_interval_hours = 24
        mock_settings.backup_postgres_retention_days = 14
        mock_settings.backup_config_retention_days = 30

        tiers = await BackupService._gcs_status("test-bucket")

    postgres = next(t for t in tiers if t["tier"] == "postgres")
    assert postgres["status"] == "unknown"
    assert postgres["backup_count"] == 0


@pytest.mark.asyncio
async def test_gcs_status_old_backup_shows_warning():
    """With a backup older than 2x interval, shows warning."""
    from app.services.backup_service import BackupService

    old = datetime.now(timezone.utc) - timedelta(hours=50)
    old_name = f"postgres/pgdump-{old.strftime('%Y%m%d-%H%M%S')}.dump"
    adapter = _mock_status_adapter([old_name])

    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.backup_service.settings") as mock_settings,
    ):
        mock_settings.backup_postgres_interval_hours = 24
        mock_settings.backup_postgres_retention_days = 14
        mock_settings.backup_config_retention_days = 30

        tiers = await BackupService._gcs_status("test-bucket")

    postgres = next(t for t in tiers if t["tier"] == "postgres")
    assert postgres["status"] == "warning"


@pytest.mark.asyncio
async def test_gcs_status_versioning_label_is_cloud_neutral():
    """The versioning tier name follows the object_label (GCS / S3), not a hardcode."""
    from app.services.backup_service import BackupService

    adapter = _mock_status_adapter([], versioning=True)
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.backup_service.settings") as mock_settings,
    ):
        mock_settings.backup_postgres_interval_hours = 24
        mock_settings.backup_postgres_retention_days = 14
        mock_settings.backup_config_retention_days = 30

        gcs_tiers = await BackupService._gcs_status("test-bucket", object_label="GCS")
        s3_tiers = await BackupService._gcs_status("test-bucket", object_label="S3")

    assert next(t for t in gcs_tiers if t["tier"] == "gcs")["name"] == "GCS Object Versioning"
    assert next(t for t in s3_tiers if t["tier"] == "gcs")["name"] == "S3 Object Versioning"
    # The fallback path (object store unavailable) is cloud-neutral too.
    assert next(t for t in BackupService._fallback_status("S3") if t["tier"] == "gcs")["name"] == (
        "S3 Object Versioning"
    )


@pytest.mark.asyncio
async def test_gcs_status_terraform_state_real_versioning_check():
    """Terraform State tier reflects the real state-bucket versioning, not a hardcode."""
    from app.services.backup_service import BackupService

    # Versioning disabled on the state bucket -> warning (was always healthy/true).
    adapter = _mock_status_adapter([], versioning=False)
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.backup_service.settings") as mock_settings,
    ):
        mock_settings.backup_postgres_interval_hours = 24
        mock_settings.backup_postgres_retention_days = 14
        mock_settings.backup_config_retention_days = 30

        tiers = await BackupService._gcs_status("test-bucket", object_label="S3", tfstate_bucket="tfstate-bucket")

    tf = next(t for t in tiers if t["tier"] == "terraform_state")
    assert tf["status"] == "warning"
    assert tf["versioning_enabled"] is False

    # With no state bucket known, it falls back to the prior healthy/true display.
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.backup_service.settings") as mock_settings,
    ):
        mock_settings.backup_postgres_interval_hours = 24
        mock_settings.backup_postgres_retention_days = 14
        mock_settings.backup_config_retention_days = 30
        tiers_no_tf = await BackupService._gcs_status("test-bucket", object_label="GCS")
    tf_default = next(t for t in tiers_no_tf if t["tier"] == "terraform_state")
    assert tf_default["status"] == "healthy"
    assert tf_default["versioning_enabled"] is True


# --- pg_dump at the process boundary ---

SENTINEL = "sentinel-5e02-ssn-987-65-4321"


def _fake_pg_dump(monkeypatch, *, exit_code: int = 0, stderr: str = "", content: bytes = b"PGDMP fake dump", gate=None):
    """Replace pg_dump with a real child that writes ``content`` to its -f path, prints ``stderr`` and exits."""
    real_exec = asyncio_mod.subprocess.create_subprocess_exec
    spawned: list[list[str]] = []

    async def exec_(*argv, **kwargs):
        spawned.append(list(argv))
        if gate is not None:
            await gate.wait()
        out = argv[argv.index("-f") + 1]
        script = (
            "import sys; open(sys.argv[1], 'wb').write(sys.argv[2].encode()); "
            "sys.stderr.write(sys.argv[3]); sys.exit(int(sys.argv[4]))"
        )
        return await real_exec(sys.executable, "-c", script, out, content.decode(), stderr, str(exit_code), **kwargs)

    monkeypatch.setattr(asyncio_mod, "create_subprocess_exec", exec_)
    return spawned


def _backup_session():
    session = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = [("config_backups_bucket_name", "my-backups-bucket")]
    session.execute = AsyncMock(return_value=result)
    return session


def _backup_adapter(*, exists: bool = False):
    seen: dict = {}
    adapter = AsyncMock()
    adapter.build_uri = MagicMock(side_effect=lambda bucket, key: f"gs://{bucket}/{key.lstrip('/')}")
    adapter.list_objects.return_value = []  # rotation finds nothing to delete
    adapter.exists.return_value = exists

    async def upload(uri, path, **kwargs):
        p = Path(path)
        seen.update(
            path=p,
            bytes=p.read_bytes(),
            mode=stat.S_IMODE(p.stat().st_mode),
            dir_mode=stat.S_IMODE(p.parent.stat().st_mode),
        )

    adapter.upload_filename.side_effect = upload
    return adapter, seen


@pytest.fixture
def backup_db_url(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://bioaf_app:devpassword@postgres:5432/bioaf")


@pytest.mark.asyncio
async def test_run_postgres_backup_uploads_to_gcs(monkeypatch, backup_db_url):
    """pg_dump runs, uploads to GCS, and cleans up the temp file."""
    from app.services.backup_service import BackupService

    _fake_pg_dump(monkeypatch)
    adapter, seen = _backup_adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        result = await BackupService.run_postgres_backup(_backup_session(), org_id=1)

    assert result["status"] == "completed"
    assert result["filename"].startswith("pgdump-")
    # Verify upload went through the storage adapter
    adapter.upload_filename.assert_awaited_once()
    assert adapter.upload_filename.call_args.args[0].startswith("gs://my-backups-bucket/postgres/pgdump-")
    assert seen["bytes"] == b"PGDMP fake dump"
    assert not seen["path"].exists()


@pytest.mark.asyncio
async def test_the_dump_is_written_privately_under_a_name_the_code_chooses(monkeypatch, backup_db_url):
    from app.config import settings
    from app.services.backup_service import BackupService

    _fake_pg_dump(monkeypatch)
    adapter, seen = _backup_adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        await BackupService.run_postgres_backup(_backup_session(), org_id=1)
    assert seen["mode"] == 0o600 and seen["dir_mode"] == 0o700
    assert seen["path"].parent.parent == Path(settings.backup_scratch_dir)
    assert list(Path(settings.backup_scratch_dir).iterdir()) == []


@pytest.mark.asyncio
async def test_a_failed_pg_dump_reports_a_reference_not_its_stderr(monkeypatch, backup_db_url, caplog):
    from app.config import settings
    from app.services.backup_service import BackupService

    caplog.set_level(logging.DEBUG)
    _fake_pg_dump(monkeypatch, exit_code=1, stderr=f"pg_dump: error: query failed: ERROR: {SENTINEL} password=hunter2")
    adapter, _ = _backup_adapter()
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.backup_service.log_action", new_callable=AsyncMock) as audit,
    ):
        result = await BackupService.run_postgres_backup(_backup_session(), org_id=1)

    assert result["status"] == "error"
    assert result["reference"] in result["message"]
    for sink in (str(result), caplog.text, str(audit.call_args_list)):
        assert SENTINEL not in sink and "hunter2" not in sink
    assert result["reference"] in caplog.text
    assert audit.call_args.kwargs["action"] == "backup_failed"
    assert audit.call_args.kwargs["details"]["reference"] == result["reference"]
    adapter.upload_filename.assert_not_awaited()
    assert list(Path(settings.backup_scratch_dir).iterdir()) == []


@pytest.mark.asyncio
async def test_an_upload_failure_is_reported_without_its_text(monkeypatch, backup_db_url, caplog):
    from app.services.backup_service import BackupService

    caplog.set_level(logging.DEBUG)
    _fake_pg_dump(monkeypatch)
    adapter, _ = _backup_adapter()
    adapter.upload_filename.side_effect = RuntimeError(f"403 for gs://bucket?X-Goog-Signature={SENTINEL}")
    with (
        patch("app.adapters.registry.get_storage_adapter", return_value=adapter),
        patch("app.services.backup_service.log_action", new_callable=AsyncMock) as audit,
    ):
        result = await BackupService.run_postgres_backup(_backup_session(), org_id=1)
    assert result["status"] == "error"
    for sink in (str(result), caplog.text, str(audit.call_args_list)):
        assert SENTINEL not in sink
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_an_existing_backup_with_the_same_name_is_never_overwritten(monkeypatch, backup_db_url):
    from app.services.backup_service import BackupService

    _fake_pg_dump(monkeypatch)
    adapter, _ = _backup_adapter(exists=True)
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        result = await BackupService.run_postgres_backup(_backup_session(), org_id=1)
    assert result["status"] == "error" and "already exists" in result["message"]
    adapter.upload_filename.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_backup_while_one_runs_is_refused(monkeypatch, backup_db_url, client, admin_token):
    from app.services.backup_service import BackupService

    gate = asyncio_mod.Event()
    spawned = _fake_pg_dump(monkeypatch, gate=gate)
    adapter, _ = _backup_adapter()
    with patch("app.adapters.registry.get_storage_adapter", return_value=adapter):
        first = asyncio_mod.create_task(BackupService.run_postgres_backup(_backup_session(), org_id=1))
        while not spawned:
            await asyncio_mod.sleep(0.01)
        second = await BackupService.run_postgres_backup(_backup_session(), org_id=1)
        response = await client.post(
            "/api/backups/trigger/postgres", headers={"Authorization": f"Bearer {admin_token}"}
        )
        gate.set()
        assert (await first)["status"] == "completed"
    assert second["status"] == "error" and second["reason"] == "busy"
    assert response.status_code == 409
    assert len(spawned) == 1


@pytest.mark.asyncio
async def test_run_postgres_backup_no_bucket_configured():
    """Returns error if no backups bucket is configured in platform_config."""
    from app.services.backup_service import BackupService

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    result = await BackupService.run_postgres_backup(mock_session, org_id=1)

    assert result["status"] == "error"
    assert "bucket" in result["message"].lower()
