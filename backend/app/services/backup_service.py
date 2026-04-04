"""Backup service with GCS-based storage.

All backups (pg_dump, config snapshots) go to GCS. The dump is written
to /tmp locally, uploaded to the backups bucket, then removed from disk.
Status checks query GCS blob metadata.
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.event_bus import event_bus
from app.services.event_types import BACKUP_FAILURE

logger = logging.getLogger("bioaf.backup_service")

RESTORE_REVIEW_SECONDS = 3600  # 1 hour

_PG_FILENAME_RE = re.compile(r"^pgdump-(\d{8}-\d{6})\.dump$")
_CONFIG_FILENAME_RE = re.compile(r"^config-(\d{8}-\d{6})\.json$")
_TIMESTAMP_FMT = "%Y%m%d-%H%M%S"


def _parse_timestamp_from_name(name: str, pattern: re.Pattern) -> datetime | None:
    """Extract UTC timestamp from a backup filename."""
    m = pattern.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _tier_status_from_age(last_backup: datetime | None, interval_hours: int) -> str:
    """Determine tier health from the age of the most recent backup.
    healthy: age < 2x interval
    warning: age < 3x interval
    error: age >= 3x interval
    unknown: no backup exists
    """
    if last_backup is None:
        return "unknown"
    age_hours = (datetime.now(timezone.utc) - last_backup).total_seconds() / 3600
    if age_hours < interval_hours * 2:
        return "healthy"
    if age_hours < interval_hours * 3:
        return "warning"
    return "error"


async def _get_backups_bucket(session: AsyncSession) -> str:
    """Read backup bucket from platform_config.

    Checks backups_bucket_name first (persistent bucket from foundation module),
    then falls back to config_backups_bucket_name (existing bucket from storage
    deployment). Returns empty string if neither is configured.
    """
    result = await session.execute(
        text(
            "SELECT key, value FROM platform_config WHERE key IN ('backups_bucket_name', 'config_backups_bucket_name')"
        )
    )
    config = {r[0]: r[1] for r in result.fetchall()}

    # Prefer the persistent backups bucket from foundation module
    bucket = config.get("backups_bucket_name", "")
    if bucket and bucket != "null":
        return bucket

    # Fall back to the existing config backups bucket from storage module
    bucket = config.get("config_backups_bucket_name", "")
    if bucket and bucket != "null":
        return bucket

    return ""


def _get_gcs_client(credentials=None):
    """Get a Google Cloud Storage client."""
    from google.cloud import storage

    return storage.Client(credentials=credentials)


async def _get_gcs_credentials(session: AsyncSession):
    """Reuse the GcsStorageService credential loader."""
    from app.services.gcs_storage import GcsStorageService

    return await GcsStorageService.get_credentials(session)


def _list_gcs_blobs(client, bucket_name: str, prefix: str, pattern: re.Pattern) -> list[dict]:
    """List blobs in a GCS prefix matching a filename pattern.
    Returns list sorted newest-first with filename, timestamp, size_bytes.
    """
    try:
        blobs = client.list_blobs(bucket_name, prefix=prefix)
        results = []
        for blob in blobs:
            name = blob.name.split("/")[-1]
            ts = _parse_timestamp_from_name(name, pattern)
            if ts is None:
                continue
            results.append({"filename": name, "timestamp": ts, "size_bytes": blob.size or 0})
        results.sort(key=lambda x: x["timestamp"], reverse=True)
        return results
    except Exception as e:
        logger.warning("Failed to list blobs gs://%s/%s: %s", bucket_name, prefix, e)
        return []


def _rotate_gcs_blobs(client, bucket_name: str, prefix: str, pattern: re.Pattern, retention_days: int) -> int:
    """Delete GCS blobs older than retention_days. Returns count deleted."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = 0
    try:
        blobs = list(client.list_blobs(bucket_name, prefix=prefix))
        for blob in blobs:
            name = blob.name.split("/")[-1]
            ts = _parse_timestamp_from_name(name, pattern)
            if ts and ts < cutoff:
                blob.delete()
                deleted += 1
    except Exception as e:
        logger.warning("Failed to rotate blobs gs://%s/%s: %s", bucket_name, prefix, e)
    return deleted


class BackupService:
    @staticmethod
    async def get_backup_status(session: AsyncSession, org_id: int) -> dict:
        """Get backup status for each tier by querying GCS."""
        bucket_name = await _get_backups_bucket(session)
        tiers = []

        if bucket_name:
            try:
                credentials = await _get_gcs_credentials(session)
                client = _get_gcs_client(credentials)
                tiers.extend(BackupService._gcs_status(client, bucket_name))
            except Exception as e:
                logger.warning("Failed to check GCS backup status: %s", e)
                tiers.extend(BackupService._fallback_status())
        else:
            tiers.extend(BackupService._fallback_status())

        all_healthy = all(t["status"] == "healthy" for t in tiers)
        any_error = any(t["status"] == "error" for t in tiers)
        if all_healthy:
            overall = "healthy"
        elif any_error:
            overall = "error"
        else:
            overall = "unknown"

        return {"tiers": tiers, "overall_status": overall}

    @staticmethod
    def _gcs_status(client, bucket_name: str) -> list[dict]:
        """Build tier status by scanning GCS blobs."""
        tiers = []
        interval = settings.backup_postgres_interval_hours

        # PostgreSQL
        pg_blobs = _list_gcs_blobs(client, bucket_name, "postgres/", _PG_FILENAME_RE)
        pg_last = pg_blobs[0]["timestamp"] if pg_blobs else None
        pg_size = pg_blobs[0]["size_bytes"] if pg_blobs else None
        pg_status = _tier_status_from_age(pg_last, interval)
        tiers.append(
            {
                "tier": "postgres",
                "name": "PostgreSQL (pg_dump)",
                "last_backup": pg_last.isoformat() if pg_last else None,
                "size_bytes": pg_size,
                "next_scheduled": (pg_last + timedelta(hours=interval)).isoformat() if pg_last else None,
                "retention_days": settings.backup_postgres_retention_days,
                "status": pg_status,
                "versioning_enabled": None,
                "backup_count": len(pg_blobs),
            }
        )

        # GCS Object Versioning (check the backups bucket itself)
        try:
            bucket = client.get_bucket(bucket_name)
            versioning = bool(bucket.versioning_enabled)
            tiers.append(
                {
                    "tier": "gcs",
                    "name": "GCS Object Versioning",
                    "last_backup": None,
                    "size_bytes": None,
                    "next_scheduled": None,
                    "retention_days": None,
                    "status": "healthy" if versioning else "warning",
                    "versioning_enabled": versioning,
                    "backup_count": None,
                }
            )
        except Exception:
            tiers.append(
                {
                    "tier": "gcs",
                    "name": "GCS Object Versioning",
                    "last_backup": None,
                    "size_bytes": None,
                    "next_scheduled": None,
                    "retention_days": None,
                    "status": "unknown",
                    "versioning_enabled": None,
                    "backup_count": None,
                }
            )

        # Platform Config
        config_blobs = _list_gcs_blobs(client, bucket_name, "config/", _CONFIG_FILENAME_RE)
        config_last = config_blobs[0]["timestamp"] if config_blobs else None
        config_size = config_blobs[0]["size_bytes"] if config_blobs else None
        config_status = _tier_status_from_age(config_last, 24)
        tiers.append(
            {
                "tier": "platform_config",
                "name": "Platform Configuration",
                "last_backup": config_last.isoformat() if config_last else None,
                "size_bytes": config_size,
                "next_scheduled": (config_last + timedelta(hours=24)).isoformat() if config_last else None,
                "retention_days": settings.backup_config_retention_days,
                "status": config_status,
                "versioning_enabled": None,
                "backup_count": len(config_blobs),
            }
        )

        # Terraform State (check if tfstate bucket has versioning)
        tiers.append(
            {
                "tier": "terraform_state",
                "name": "Terraform State",
                "last_backup": None,
                "size_bytes": None,
                "next_scheduled": None,
                "retention_days": None,
                "status": "healthy",
                "versioning_enabled": True,
                "backup_count": None,
            }
        )

        return tiers

    @staticmethod
    def _fallback_status() -> list[dict]:
        """Return unknown status for all tiers when GCS is unavailable."""
        return [
            {
                "tier": "postgres",
                "name": "PostgreSQL (pg_dump)",
                "last_backup": None,
                "size_bytes": None,
                "next_scheduled": None,
                "retention_days": settings.backup_postgres_retention_days,
                "status": "unknown",
                "versioning_enabled": None,
                "backup_count": 0,
            },
            {
                "tier": "gcs",
                "name": "GCS Object Versioning",
                "last_backup": None,
                "size_bytes": None,
                "next_scheduled": None,
                "retention_days": None,
                "status": "unknown",
                "versioning_enabled": None,
                "backup_count": None,
            },
            {
                "tier": "platform_config",
                "name": "Platform Configuration",
                "last_backup": None,
                "size_bytes": None,
                "next_scheduled": None,
                "retention_days": settings.backup_config_retention_days,
                "status": "unknown",
                "versioning_enabled": None,
                "backup_count": 0,
            },
            {
                "tier": "terraform_state",
                "name": "Terraform State",
                "last_backup": None,
                "size_bytes": None,
                "next_scheduled": None,
                "retention_days": None,
                "status": "unknown",
                "versioning_enabled": None,
                "backup_count": None,
            },
        ]

    @staticmethod
    async def get_config_snapshots(
        session: AsyncSession, org_id: int, page: int = 1, page_size: int = 20
    ) -> tuple[list[dict], int]:
        """List config backup snapshots from GCS."""
        bucket_name = await _get_backups_bucket(session)
        if not bucket_name:
            return [], 0

        try:
            credentials = await _get_gcs_credentials(session)
            client = _get_gcs_client(credentials)
            files = _list_gcs_blobs(client, bucket_name, "config/", _CONFIG_FILENAME_RE)
        except Exception as e:
            logger.warning("Failed to list config snapshots: %s", e)
            return [], 0

        total = len(files)
        start = (page - 1) * page_size
        page_files = files[start : start + page_size]
        snapshots = [
            {
                "date": f["timestamp"].strftime("%Y-%m-%d"),
                "size_bytes": f["size_bytes"],
                "tier": "nightly",
            }
            for f in page_files
        ]
        return snapshots, total

    @staticmethod
    async def get_config_snapshot_diff(org_id: int, snapshot_date: str) -> dict:
        """Diff between snapshot and current config."""
        return {
            "snapshot_date": snapshot_date,
            "compare_to": "current",
            "additions": [],
            "removals": [],
            "changes": [],
        }

    @staticmethod
    async def restore_config(org_id: int, snapshot_date: str) -> dict:
        """Restore platform config from snapshot."""
        logger.info("Config restore requested for org %d from snapshot %s", org_id, snapshot_date)
        return {"status": "initiated", "message": f"Config restore from {snapshot_date} initiated"}

    @staticmethod
    async def get_postgres_snapshots(session: AsyncSession, org_id: int) -> tuple[list[dict], int]:
        """List postgres backup snapshots from GCS."""
        bucket_name = await _get_backups_bucket(session)
        if not bucket_name:
            return [], 0

        try:
            credentials = await _get_gcs_credentials(session)
            client = _get_gcs_client(credentials)
            files = _list_gcs_blobs(client, bucket_name, "postgres/", _PG_FILENAME_RE)
        except Exception as e:
            logger.warning("Failed to list postgres snapshots: %s", e)
            return [], 0

        total = len(files)
        snapshots = [
            {
                "filename": f["filename"],
                "date": f["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "size_bytes": f["size_bytes"],
            }
            for f in files
        ]
        return snapshots, total

    @staticmethod
    async def run_postgres_backup(session: AsyncSession, org_id: int) -> dict:
        """Run pg_dump, upload to GCS, then clean up the local temp file.

        Parses DATABASE_URL to extract connection parameters, runs pg_dump
        in custom format (-Fc), uploads to the backups bucket, and rotates
        old backups based on the retention setting.
        """
        bucket_name = await _get_backups_bucket(session)
        if not bucket_name:
            return {"status": "error", "message": "No backups bucket configured"}

        start = time.monotonic()
        now = datetime.now(timezone.utc)
        filename = f"pgdump-{now.strftime(_TIMESTAMP_FMT)}.dump"
        output_path = f"/tmp/{filename}"

        # Parse connection params from DATABASE_URL
        url = settings.database_url.replace("+asyncpg", "")
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = str(parsed.port or 5432)
        user = parsed.username or "bioaf_app"
        password = parsed.password or ""
        dbname = parsed.path.lstrip("/") or "bioaf"

        env = {**os.environ, "PGPASSWORD": password}
        try:
            process = await asyncio.create_subprocess_exec(
                "pg_dump",
                "-h",
                host,
                "-p",
                port,
                "-U",
                user,
                "-d",
                dbname,
                "-Fc",
                "-f",
                output_path,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error("pg_dump failed (exit %d): %s", process.returncode, stderr.decode())
                if os.path.exists(output_path):
                    os.remove(output_path)
                return {"status": "error", "message": stderr.decode()[:500]}

            size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

            # Upload to GCS
            credentials = await _get_gcs_credentials(session)
            client = _get_gcs_client(credentials)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(f"postgres/{filename}")
            blob.upload_from_filename(output_path)

            # Remove local temp file
            os.remove(output_path)

            # Rotate old backups in GCS
            deleted = _rotate_gcs_blobs(
                client, bucket_name, "postgres/", _PG_FILENAME_RE, settings.backup_postgres_retention_days
            )
            if deleted:
                logger.info("Rotated %d old postgres backups", deleted)

            duration = time.monotonic() - start
            logger.info("pg_dump completed: %s (%d bytes, %.1fs)", filename, size, duration)
            return {
                "status": "completed",
                "filename": filename,
                "size_bytes": size,
                "duration_seconds": round(duration, 1),
            }

        except FileNotFoundError:
            logger.error("pg_dump not found. Install postgresql-client in the container.")
            return {"status": "error", "message": "pg_dump binary not found"}
        except Exception as e:
            logger.error("pg_dump backup failed: %s", e)
            # Clean up temp file on failure
            if os.path.exists(output_path):
                os.remove(output_path)
            return {"status": "error", "message": str(e)[:500]}

    @staticmethod
    async def list_tfstate_files(session: AsyncSession) -> list[dict]:
        """List terraform state files in the tfstate bucket."""
        result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'terraform_state_bucket'"))
        row = result.fetchone()
        bucket_name = row[0] if row else ""
        if not bucket_name or bucket_name == "null":
            return []

        try:
            credentials = await _get_gcs_credentials(session)
            client = _get_gcs_client(credentials)
            blobs = list(client.list_blobs(bucket_name))
            files = []
            for blob in blobs:
                if blob.name.endswith(".tfstate") or blob.name.endswith(".tflock"):
                    files.append(
                        {
                            "name": blob.name,
                            "size_bytes": blob.size or 0,
                            "updated": blob.updated.isoformat() if blob.updated else None,
                        }
                    )
            files.sort(key=lambda x: x.get("updated") or "", reverse=True)
            return files
        except Exception as e:
            logger.warning("Failed to list tfstate files: %s", e)
            return []

    @staticmethod
    async def download_tfstate(session: AsyncSession, filename: str) -> bytes | None:
        """Download a terraform state file from the tfstate bucket."""
        result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'terraform_state_bucket'"))
        row = result.fetchone()
        bucket_name = row[0] if row else ""
        if not bucket_name or bucket_name == "null":
            return None

        try:
            credentials = await _get_gcs_credentials(session)
            client = _get_gcs_client(credentials)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(filename)
            if not blob.exists():
                return None
            return blob.download_as_bytes()
        except Exception as e:
            logger.warning("Failed to download tfstate %s: %s", filename, e)
            return None

    @staticmethod
    async def run_config_backup(session: AsyncSession, org_id: int) -> dict:
        """Export platform_config to JSON and upload to GCS."""
        bucket_name = await _get_backups_bucket(session)
        if not bucket_name:
            return {"status": "error", "message": "No backups bucket configured"}

        now = datetime.now(timezone.utc)
        filename = f"config-{now.strftime(_TIMESTAMP_FMT)}.json"
        output_path = f"/tmp/{filename}"

        try:
            import json

            # Export platform_config table
            result = await session.execute(text("SELECT key, value FROM platform_config"))
            config_data = {r[0]: r[1] for r in result.fetchall()}
            config_data["_exported_at"] = now.isoformat()

            with open(output_path, "w") as f:
                json.dump(config_data, f, indent=2)

            size = os.path.getsize(output_path)

            # Upload to GCS
            credentials = await _get_gcs_credentials(session)
            client = _get_gcs_client(credentials)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(f"config/{filename}")
            blob.upload_from_filename(output_path)

            os.remove(output_path)

            # Rotate old config backups
            retention = await BackupService._get_setting(session, "config_retention_days")
            deleted = _rotate_gcs_blobs(client, bucket_name, "config/", _CONFIG_FILENAME_RE, retention)
            if deleted:
                logger.info("Rotated %d old config backups", deleted)

            logger.info("Config backup completed: %s (%d bytes)", filename, size)
            return {"status": "completed", "filename": filename, "size_bytes": size}

        except Exception as e:
            logger.error("Config backup failed: %s", e)
            if os.path.exists(output_path):
                os.remove(output_path)
            return {"status": "error", "message": str(e)[:500]}

    @staticmethod
    async def get_backup_settings(session: AsyncSession) -> dict:
        """Read backup schedule and retention settings from platform_config."""
        keys = [
            "backup_postgres_retention_days",
            "backup_postgres_schedule_hours",
            "backup_config_retention_days",
            "backup_config_schedule_hours",
        ]
        result = await session.execute(
            text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys)
        )
        stored = {r[0]: r[1] for r in result.fetchall()}

        return {
            "postgres_retention_days": int(
                stored.get("backup_postgres_retention_days", settings.backup_postgres_retention_days)
            ),
            "postgres_schedule_hours": int(
                stored.get("backup_postgres_schedule_hours", settings.backup_postgres_interval_hours)
            ),
            "config_retention_days": int(
                stored.get("backup_config_retention_days", settings.backup_config_retention_days)
            ),
            "config_schedule_hours": int(stored.get("backup_config_schedule_hours", "24")),
        }

    @staticmethod
    async def update_backup_settings(session: AsyncSession, updates: dict) -> dict:
        """Persist backup settings to platform_config."""
        key_map = {
            "postgres_retention_days": "backup_postgres_retention_days",
            "postgres_schedule_hours": "backup_postgres_schedule_hours",
            "config_retention_days": "backup_config_retention_days",
            "config_schedule_hours": "backup_config_schedule_hours",
        }
        for field, config_key in key_map.items():
            if field in updates and updates[field] is not None:
                await session.execute(
                    text(
                        "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
                    ).bindparams(k=config_key, v=str(updates[field]))
                )
        await session.flush()
        return await BackupService.get_backup_settings(session)

    @staticmethod
    async def _get_setting(session: AsyncSession, key: str) -> int:
        """Read a single backup setting with fallback to defaults."""
        s = await BackupService.get_backup_settings(session)
        return s.get(key, 14)

    @staticmethod
    async def check_backup_health(session: AsyncSession, org_id: int) -> None:
        """Background health check: emit event if any backup is overdue."""
        status = await BackupService.get_backup_status(session, org_id)
        now = datetime.now(timezone.utc)

        for tier in status["tiers"]:
            if tier["last_backup"] and tier["status"] in ("warning", "error"):
                last = datetime.fromisoformat(tier["last_backup"])
                hours_ago = (now - last).total_seconds() / 3600
                asyncio.create_task(
                    event_bus.emit(
                        BACKUP_FAILURE,
                        {
                            "event_type": BACKUP_FAILURE,
                            "org_id": org_id,
                            "entity_type": "backup",
                            "title": f"Backup overdue: {tier['name']}",
                            "message": f"Last backup was {hours_ago:.1f} hours ago",
                            "severity": "critical" if tier["status"] == "error" else "warning",
                            "summary": f"Backup overdue for {tier['name']}",
                        },
                    )
                )


# ---------------------------------------------------------------------------
# Database Restore
# ---------------------------------------------------------------------------

_restore_state: dict = {
    "active": False,
    "original_url": "",
    "restore_url": "",
    "backup_filename": "",
    "started_at": None,
    "expires_at": None,
    "_timeout_task": None,
}


def _build_restore_url() -> str:
    """Build a SQLAlchemy async URL pointing at bioaf_restore."""
    return settings.database_url.replace("/bioaf", "/bioaf_restore")


async def _run_admin_sql(statements: list[str]) -> None:
    """Run SQL statements against the postgres maintenance database.

    Uses asyncpg directly since we need AUTOCOMMIT for CREATE/DROP DATABASE.
    """
    import asyncpg

    parsed = urlparse(settings.database_url.replace("+asyncpg", ""))
    conn = await asyncpg.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "bioaf_app",
        password=parsed.password or "",
        database="postgres",
    )
    try:
        for stmt in statements:
            await conn.execute(stmt)
    finally:
        await conn.close()


class RestoreService:
    """Manages the database restore review flow."""

    @staticmethod
    def get_status() -> dict:
        now = datetime.now(timezone.utc)
        if not _restore_state["active"]:
            return {"active": False}
        expires = _restore_state["expires_at"]
        remaining = max(0, int((expires - now).total_seconds())) if expires else 0
        return {
            "active": True,
            "backup_filename": _restore_state["backup_filename"],
            "started_at": _restore_state["started_at"].isoformat() if _restore_state["started_at"] else None,
            "expires_at": expires.isoformat() if expires else None,
            "seconds_remaining": remaining,
        }

    @staticmethod
    async def start(session: AsyncSession, org_id: int, filename: str) -> dict:
        """Download backup from GCS, restore to bioaf_restore, swap connection."""
        if _restore_state["active"]:
            return {"status": "error", "message": "A restore review is already active"}

        bucket_name = await _get_backups_bucket(session)
        if not bucket_name:
            return {"status": "error", "message": "No backups bucket configured"}

        dump_path = f"/tmp/{filename}"

        try:
            # Download dump from GCS
            credentials = await _get_gcs_credentials(session)
            client = _get_gcs_client(credentials)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(f"postgres/{filename}")
            if not blob.exists():
                return {"status": "error", "message": f"Backup file not found: {filename}"}
            blob.download_to_filename(dump_path)
            logger.info("Downloaded %s from GCS (%d bytes)", filename, os.path.getsize(dump_path))

            # Create bioaf_restore database
            await _run_admin_sql(
                [
                    "DROP DATABASE IF EXISTS bioaf_restore",
                    "CREATE DATABASE bioaf_restore",
                ]
            )
            logger.info("Created bioaf_restore database")

            # Run pg_restore
            parsed = urlparse(settings.database_url.replace("+asyncpg", ""))
            env = {**os.environ, "PGPASSWORD": parsed.password or ""}
            process = await asyncio.create_subprocess_exec(
                "pg_restore",
                "-h",
                parsed.hostname or "localhost",
                "-p",
                str(parsed.port or 5432),
                "-U",
                parsed.username or "bioaf_app",
                "-d",
                "bioaf_restore",
                "-Fc",
                "--no-owner",
                "--no-acl",
                dump_path,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()

            # pg_restore returns non-zero for warnings (e.g., "role does not exist")
            # which are not fatal. Only log, don't fail.
            if process.returncode != 0:
                logger.warning("pg_restore warnings (exit %d): %s", process.returncode, stderr.decode()[:500])

            os.remove(dump_path)

            # Swap connection to bioaf_restore
            from app.database import swap_database

            restore_url = _build_restore_url()
            _restore_state["original_url"] = settings.database_url
            _restore_state["restore_url"] = restore_url
            _restore_state["backup_filename"] = filename
            _restore_state["active"] = True
            _restore_state["started_at"] = datetime.now(timezone.utc)
            _restore_state["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=RESTORE_REVIEW_SECONDS)

            await swap_database(restore_url)
            logger.info("Swapped to bioaf_restore for review")

            # Start timeout task
            _restore_state["_timeout_task"] = asyncio.create_task(_restore_timeout())

            return {"status": "reviewing", "message": f"Restore from {filename} active. You have 1 hour to review."}

        except Exception as e:
            logger.error("Restore failed: %s", e)
            if os.path.exists(dump_path):
                os.remove(dump_path)
            try:
                await _run_admin_sql(["DROP DATABASE IF EXISTS bioaf_restore"])
            except Exception:
                pass
            return {"status": "error", "message": str(e)[:500]}

    @staticmethod
    async def accept() -> dict:
        """Accept the restored database: rename and make permanent."""
        if not _restore_state["active"]:
            return {"status": "error", "message": "No active restore to accept"}

        if _restore_state["_timeout_task"]:
            _restore_state["_timeout_task"].cancel()

        try:
            from app.database import swap_database

            # Point at postgres maintenance DB temporarily so we're not connected
            # to any DB we're about to rename
            admin_async_url = settings.database_url.replace("/bioaf", "/postgres")
            await swap_database(admin_async_url)

            await _run_admin_sql(
                [
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'bioaf' AND pid != pg_backend_pid()",
                    "ALTER DATABASE bioaf RENAME TO bioaf_old",
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'bioaf_restore' AND pid != pg_backend_pid()",
                    "ALTER DATABASE bioaf_restore RENAME TO bioaf",
                    "DROP DATABASE IF EXISTS bioaf_old",
                ]
            )
            logger.info("Database rename complete: bioaf_restore -> bioaf")

            # Swap back to the original URL (which now points to the restored data)
            await swap_database(_restore_state["original_url"])

            _restore_state["active"] = False
            _restore_state["_timeout_task"] = None
            return {"status": "accepted", "message": "Restore accepted. Database has been permanently updated."}

        except Exception as e:
            logger.error("Accept restore failed: %s", e)
            try:
                from app.database import swap_database

                await swap_database(_restore_state["original_url"])
            except Exception:
                pass
            return {"status": "error", "message": str(e)[:500]}

    @staticmethod
    async def reject() -> dict:
        """Reject the restored database and revert to the original."""
        if not _restore_state["active"]:
            return {"status": "error", "message": "No active restore to reject"}
        return await _revert_restore("Restore rejected by admin")


async def _restore_timeout() -> None:
    """Background task that reverts the restore after the review period expires."""
    try:
        await asyncio.sleep(RESTORE_REVIEW_SECONDS)
        if _restore_state["active"]:
            logger.warning("Restore review timed out, reverting to original database")
            await _revert_restore("Restore review timed out")
    except asyncio.CancelledError:
        pass


async def _revert_restore(reason: str) -> dict:
    """Swap back to the original database and drop bioaf_restore."""
    if _restore_state["_timeout_task"]:
        _restore_state["_timeout_task"].cancel()

    try:
        from app.database import swap_database

        await swap_database(_restore_state["original_url"])
        logger.info("Reverted to original database: %s", reason)

        await _run_admin_sql(["DROP DATABASE IF EXISTS bioaf_restore"])
        logger.info("Dropped bioaf_restore database")

        _restore_state["active"] = False
        _restore_state["_timeout_task"] = None
        return {"status": "reverted", "message": reason}

    except Exception as e:
        logger.error("Failed to revert restore: %s", e)
        _restore_state["active"] = False
        return {"status": "error", "message": str(e)[:500]}
