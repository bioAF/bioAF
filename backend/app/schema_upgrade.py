"""The one database upgrade lifecycle every entry point goes through (plan 9, section 3).

Every way of changing the schema reaches Alembic's production ``env.py``, and ``env.py`` hands the
connection to :func:`run`, so the container entrypoint, ``./bioaf migrate``, the update agent and an
old updater's own ``alembic upgrade head`` all get the same steps:

1. **Serialize.** A session advisory lock on the migration connection. A second runner waits, then
   finds nothing to do.
2. **Recovery backup.** Before any revision change: a PostgreSQL 16 custom-format dump into the
   durable backups directory, written as ``.partial`` and kept only after the dump exited 0 and its
   table of contents lists data for every table. A failed or unverifiable backup stops here: nothing
   has changed, the failure is recorded, and the run can be retried. An empty database (a fresh
   install) has nothing to protect and is not dumped.
3. **Migrate** on the locked connection (one transaction: an interruption rolls back whole).
4. **Validate** the result and **record** it in ``schema-upgrade.json`` in the update-status directory.

The application gate (:func:`compatibility`) treats the database as usable only when it is at this
code's head and the record says that upgrade completed. Writers are quiesced by that gate: the new
application does no startup writes or background work until it passes, and Compose has already
stopped the old container when it replaced it.

Recovery material: the dump plus a manifest naming the database, its revision, the server and tool
versions, the dump's sha256 and fingerprints of the encryption keys in use. The keys themselves stay
where they are (``docker/.env``); copying them next to the dump would undo what at-rest encryption is
for (ADR-047's threat model is the backup). Restoring needs that ``.env`` and this image's release.

Space: the dump needs at most the database's size, migrations need temporary room for rebuilt indexes
and rewritten tables, and the disk must not be left full. Required free space on the backups disk is
``1.5 x pg_database_size + BIOAF_UPGRADE_BACKUP_RESERVE_BYTES`` (default 512 MiB); the check runs
before any work. The dump has ``BIOAF_UPGRADE_BACKUP_TIMEOUT_SECONDS`` (default one hour). Routine
verification reads the archive's table of contents; it does not restore it (the VM has no room for a
second database). Full restores are exercised in the test suite.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from app.config import settings

logger = logging.getLogger("bioaf.schema_upgrade")

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"
STATUS_FILE = "schema-upgrade.json"
# pg_advisory_lock key: ASCII "bioafdb" as an integer. Any runner, any process, any container.
LOCK_KEY = int.from_bytes(b"bioafdb", "big")
LOCK_WAIT_LOG_SECONDS = 10

# What a completed upgrade must have, checked after migrating. Narrow on purpose: valid historical
# values (legacy NULLs and the like) must never hold an install offline.
REQUIRED_INDEXES = ("uq_samples_experiment_external_id",)


class UpgradeFailed(Exception):
    """A lifecycle step failed. The record says which; the database is as the last commit left it."""


class BackupFailed(UpgradeFailed):
    """No verified recovery backup, so nothing was changed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def status_path() -> Path:
    return Path(settings.update_status_dir) / STATUS_FILE


def read_status() -> dict[str, Any]:
    try:
        return json.loads(status_path().read_text())
    except (OSError, ValueError):
        return {}


def write_status(record: dict[str, Any]) -> None:
    """Atomically replace the record (a reader never sees half a file)."""
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1, default=str))
    os.replace(tmp, path)


def code_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None
    return head


# --- backup ---------------------------------------------------------------------------------------


def pg_bin(name: str) -> str:
    """An explicitly selected PostgreSQL 16 client binary (never whatever ``pg_dump`` is on PATH)."""
    path = Path(settings.upgrade_pg_bin_dir) / name
    if not path.exists():
        raise BackupFailed(f"{path} not found; the PostgreSQL 16 client is required for the recovery backup")
    return str(path)


def _libpq(url: str) -> tuple[str, dict[str, str]]:
    """A libpq URI without the password, and an environment carrying it (kept out of the process list)."""
    parsed = make_url(url).set(drivername="postgresql")
    env = dict(os.environ)
    if parsed.password:
        env["PGPASSWORD"] = str(parsed.password)
    return parsed.set(password=None).render_as_string(hide_password=False), env


def _version(binary: str) -> str:
    return subprocess.run([binary, "--version"], capture_output=True, text=True, check=True).stdout.strip()


def _tables(conn: Connection) -> list[str]:
    return list(
        conn.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') ORDER BY 1"
            )
        ).scalars()
    )


def verify_archive(path: Path, tables: list[str]) -> None:
    """The archive lists data for every table. Reads the table of contents only; not a restore."""
    listing = subprocess.run([pg_bin("pg_restore"), "--list", str(path)], capture_output=True, text=True)
    if listing.returncode != 0:
        raise BackupFailed(f"pg_restore --list could not read the dump: {listing.stderr.strip()[:500]}")
    with_data = {
        line.split()[-2] for line in listing.stdout.splitlines() if " TABLE DATA " in line and not line.startswith(";")
    }
    missing = sorted(set(tables) - with_data)
    if missing:
        raise BackupFailed(f"the dump has no data for {len(missing)} table(s), e.g. {missing[:5]}")


def _is_mounted(path: Path) -> bool:
    """Whether ``path`` is itself a mount point (a bind mount from the host, in Compose)."""
    resolved = str(path.resolve())
    try:
        with open("/proc/self/mountinfo") as mounts:
            if any(line.split()[4] == resolved for line in mounts):
                return True
    except OSError:
        pass
    return os.path.ismount(resolved)


def make_backup(conn: Connection, url: str, from_revision: str | None, to_revision: str) -> dict[str, Any]:
    """Write and verify the recovery dump; return its manifest. Raises BackupFailed, changing nothing."""
    backups = Path(settings.upgrade_backups_dir)
    try:
        backups.mkdir(parents=True, exist_ok=True)
        probe = backups / ".write-probe"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        raise BackupFailed(f"cannot write recovery backups to {backups}: {exc}") from exc

    if settings.upgrade_require_mounted_backups and not _is_mounted(backups):
        raise BackupFailed(
            f"{backups} is not a mounted volume, so a backup there would be lost with the container; "
            "docker-compose.yml mounts ../backups there"
        )

    db_size = int(conn.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
    needed = int(db_size * 1.5) + settings.upgrade_backup_reserve_bytes
    free = shutil.disk_usage(backups).free
    if free < needed:
        raise BackupFailed(
            f"not enough free space in {backups}: {free} bytes free, {needed} needed "
            f"(1.5 x database size {db_size} + reserve {settings.upgrade_backup_reserve_bytes})"
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"bioaf_pre_upgrade_{from_revision or 'none'}_to_{to_revision}_{stamp}"
    final = backups / f"{name}.dump"
    attempt = 2
    while final.exists():  # never replace an earlier recovery set
        final = backups / f"{name}_{attempt}.dump"
        attempt += 1
    partial = final.with_suffix(".dump.partial")
    dsn, env = _libpq(url)
    pg_dump = pg_bin("pg_dump")
    tables = _tables(conn)
    started = time.monotonic()
    try:
        done = subprocess.run(
            [pg_dump, "--format=custom", "--no-owner", "--file", str(partial), "--dbname", dsn],
            env=env,
            capture_output=True,
            text=True,
            timeout=settings.upgrade_backup_timeout_seconds,
        )
        if done.returncode != 0:
            raise BackupFailed(f"pg_dump exited {done.returncode}: {done.stderr.strip()[:500]}")
        if not partial.exists() or partial.stat().st_size == 0:
            raise BackupFailed("pg_dump reported success but wrote no dump")
        verify_archive(partial, tables)
    except subprocess.TimeoutExpired as exc:
        partial.unlink(missing_ok=True)
        raise BackupFailed(f"pg_dump did not finish in {settings.upgrade_backup_timeout_seconds}s") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    digest = hashlib.sha256(partial.read_bytes()).hexdigest()
    os.replace(partial, final)
    manifest = {
        "path": str(final),
        "format": "postgresql custom (pg_restore)",
        "database": conn.execute(text("SELECT current_database()")).scalar_one(),
        "revision": from_revision,
        "upgrading_to": to_revision,
        "server_version": conn.execute(text("SHOW server_version")).scalar_one(),
        "pg_dump": _version(pg_dump),
        "pg_restore": _version(pg_bin("pg_restore")),
        "bytes": final.stat().st_size,
        "sha256": digest,
        "tables": len(tables),
        "seconds": round(time.monotonic() - started, 1),
        "encryption_key_fingerprints": [
            hashlib.sha256(k.strip().encode()).hexdigest()[:16]
            for k in settings.encryption_keys.split(",")
            if k.strip()
        ],
        "created_at": _now(),
    }
    final.with_suffix(".json").write_text(json.dumps(manifest, indent=1))
    return manifest


# --- lifecycle ------------------------------------------------------------------------------------


def _lock(conn: Connection) -> None:
    waited = 0.0
    while not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar_one():
        if waited % LOCK_WAIT_LOG_SECONDS == 0:
            logger.info("Waiting for another database upgrade to finish (%.0fs)", waited)
            print(f"Waiting for another database upgrade to finish ({waited:.0f}s)", file=sys.stderr)
        if waited >= settings.upgrade_lock_timeout_seconds:
            raise UpgradeFailed(f"another upgrade held the lock for {waited:.0f}s")
        time.sleep(1)
        waited += 1
    # The lock query opened a transaction; Alembic must start its own.
    conn.commit()


def _unlock(conn: Connection) -> None:
    try:
        conn.rollback()
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        conn.commit()
    except Exception:  # the connection is closing anyway; the session lock goes with it
        logger.warning("Could not release the upgrade lock explicitly", exc_info=True)


def _current(conn: Connection) -> str | None:
    from alembic.migration import MigrationContext

    return MigrationContext.configure(conn).get_current_revision()


def _validate(conn: Connection, destination: str) -> dict[str, Any]:
    current = _current(conn)
    if current != (None if destination == "base" else destination):
        raise UpgradeFailed(f"database is at {current}, expected {destination}")
    summary: dict[str, Any] = {}
    if destination == code_head():
        for name in REQUIRED_INDEXES:
            if conn.execute(text("SELECT to_regclass(:n)"), {"n": name}).scalar_one() is None:
                raise UpgradeFailed(f"required index {name} is missing")
        summary["relabelled_samples"] = int(
            conn.execute(text("SELECT count(*) FROM sample_external_id_transitions")).scalar_one()
        )
    conn.rollback()
    return summary


def run(conn: Connection, url: str, destination: str | None, migrate: Callable[[Connection], None]) -> None:
    """Lock, back up, migrate, validate, record. ``destination`` None means a read-only command."""
    _lock(conn)
    try:
        if destination is None:
            migrate(conn)
            return
        current = _current(conn)
        conn.rollback()
        record: dict[str, Any] = {
            "status": "in_progress",
            "from_revision": current,
            "target_revision": destination,
            "code_head": code_head(),
            "started_at": _now(),
            "pid": os.getpid(),
        }
        try:
            if current == destination or (current is None and destination == "base"):
                migrate(conn)  # Alembic finds nothing to do
                # Validation still gates: a failure here is recorded like any other, closing the gate.
                record["step"] = "validate"
                summary = _validate(conn, destination)
                previous = read_status()
                if previous.get("status") != "completed" or previous.get("target_revision") != destination:
                    write_status(
                        {
                            **record,
                            "status": "completed",
                            "step": "validated",
                            "summary": summary,
                            "completed_at": _now(),
                        }
                    )
                return

            record["step"] = "backup"
            write_status(record)
            if current is None and not _tables(conn):
                record["backup"] = {"skipped": "empty database: a fresh install has nothing to back up"}
            else:
                record["backup"] = make_backup(conn, url, current, destination)
            conn.rollback()
            record["step"] = "migrate"
            write_status(record)
            migrate(conn)
            record["step"] = "validate"
            write_status(record)
            record["summary"] = _validate(conn, destination)
        except BaseException as exc:
            conn.rollback()
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}", failed_at=_now())
            write_status(record)
            raise
        write_status({**record, "status": "completed", "step": "validated", "completed_at": _now()})
    finally:
        _unlock(conn)


# --- application gate -----------------------------------------------------------------------------


async def compatibility(engine) -> dict[str, Any]:
    """Whether this code may run normally against the database, and why not if not."""
    head = code_head()
    record = read_status()
    try:
        async with engine.connect() as conn:
            exists = (await conn.execute(text("SELECT to_regclass('alembic_version')"))).scalar_one()
            revision = (
                (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
                if exists
                else None
            )
            free = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})).scalar_one()
            if free:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    except Exception as exc:
        return {"compatible": False, "reason": "database_unavailable", "detail": type(exc).__name__, "target": head}
    result: dict[str, Any] = {"revision": revision, "target": head, "upgrade": _public(record)}
    if not free:
        return {**result, "compatible": False, "reason": "upgrade_in_progress"}
    if revision != head:
        reason = "upgrade_failed" if record.get("status") == "failed" else "upgrade_required"
        return {**result, "compatible": False, "reason": reason}
    if record.get("status") == "failed":
        return {**result, "compatible": False, "reason": "upgrade_failed"}
    if record.get("status") != "completed" or record.get("target_revision") != head:
        return {**result, "compatible": False, "reason": "upgrade_not_validated"}
    return {**result, "compatible": True, "reason": None}


def _public(record: dict[str, Any]) -> dict[str, Any]:
    """What maintenance responses may show: no paths, hosts or fingerprints."""
    keep = ("status", "step", "from_revision", "target_revision", "started_at", "completed_at", "failed_at")
    out = {k: record[k] for k in keep if k in record}
    if "error" in record:
        out["error"] = record["error"].split(":", 1)[0]
    return out


def main(argv: list[str]) -> int:
    """``python -m app.schema_upgrade run|status``: run the lifecycle to head, or print the record."""
    if argv[:1] == ["status"]:
        # "settled": this image's own verdict, so a record left by an older image is never mistaken
        # for this one's upgrade (./bioaf update reads it to decide whether to retry).
        record, head = read_status(), code_head()
        settled = record.get("status") == "completed" and record.get("target_revision") == head
        print(json.dumps({"settled": settled, "code_head": head, "record": record}, indent=1))
        return 0
    if argv[:1] != ["run"]:
        print("usage: python -m app.schema_upgrade run|status", file=sys.stderr)
        return 2
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        print(f"Database upgrade failed: {exc}", file=sys.stderr)
        print(f"Details: {status_path()}. Retry with: python -m app.schema_upgrade run", file=sys.stderr)
        return 1
    print(f"Database at {code_head()}; record: {status_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
