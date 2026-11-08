"""A database of its own per test case, and the candidate's production upgrade path run against it.

Used by the historical upgrade matrix and the upgrade-lifecycle tests (plan 9). Each case gets a fresh
database on the test server (so migrations see ``public`` as installs do), its own recovery-backup and
update-status directories, and runs the candidate exactly as production does: a separate process with
this checkout's alembic.ini, env.py and app.schema_upgrade, configured only through ``BIOAF_*``.

Needs CREATEDB. CI's service user has it; locally use
``python -m tests.support.disposable_pg run -- python -m pytest ...``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.plugins.db_url import database_url
from tests.support import disposable_pg
from tests.support import historical_runner as runner

BACKEND = Path(__file__).resolve().parent.parent.parent
TEST_URL = database_url()


def plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _with_database(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


async def _can_create_databases() -> bool:
    conn = await asyncpg.connect(plain(TEST_URL))
    try:
        return bool(await conn.fetchval("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user"))
    finally:
        await conn.close()


def require_createdb() -> None:
    if asyncio.run(_can_create_databases()):
        return
    if os.environ.get("CI"):
        pytest.fail("these upgrade tests need CREATEDB on the test server")
    pytest.skip("needs CREATEDB: run under `python -m tests.support.disposable_pg run -- ...`")


def pg_bin_dir() -> Path:
    found = disposable_pg.find_pg_bin()
    if found is None or not (found / "pg_dump").exists():
        if os.environ.get("CI"):
            pytest.fail("the PostgreSQL 16 client (pg_dump, pg_restore) is required")
        pytest.skip("needs the PostgreSQL 16 client tools")
    return found


class CaseDatabase:
    def __init__(self) -> None:
        self.name = f"hist_{uuid.uuid4().hex[:12]}"
        self.url = _with_database(TEST_URL, self.name)
        self.dir = Path(tempfile.mkdtemp(prefix=f"bioaf-{self.name}-"))
        self.backups = self.dir / "backups"
        self.status = self.dir / "update-status"

    async def create(self) -> None:
        conn = await asyncpg.connect(plain(TEST_URL))
        try:
            await conn.execute(f'CREATE DATABASE "{self.name}"')
        finally:
            await conn.close()

    async def drop(self) -> None:
        conn = await asyncpg.connect(plain(TEST_URL))
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
        finally:
            await conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    async def connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(plain(self.url))

    def env(self, **overrides: str) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if not k.startswith(("BIOAF_", "COV_CORE_", "COVERAGE_"))}
        return {
            **base,
            "BIOAF_DATABASE_URL": self.url,
            "BIOAF_ENCRYPTION_KEYS": runner.TEST_ENCRYPTION_KEY,
            "BIOAF_JWT_SECRET_KEY": runner.TEST_JWT_SECRET,
            "BIOAF_UPGRADE_PG_BIN_DIR": str(pg_bin_dir()),
            "BIOAF_UPGRADE_BACKUPS_DIR": str(self.backups),
            "BIOAF_UPDATE_STATUS_DIR": str(self.status),
            **overrides,
        }

    def run(self, *argv: str, **env: str) -> subprocess.CompletedProcess:
        """Run a candidate command (default: an updater's own ``alembic upgrade head``)."""
        command = list(argv) or ["-m", "alembic", "-c", "alembic/alembic.ini", "upgrade", "head"]
        return subprocess.run(
            [sys.executable, *command],
            cwd=BACKEND,
            env=self.env(**env),
            capture_output=True,
            text=True,
            timeout=600,
        )

    def popen(self, *argv: str, **env: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, *argv],
            cwd=BACKEND,
            env=self.env(**env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


def case_database():
    """Generator behind each test file's ``case_db`` fixture."""
    require_createdb()
    db = CaseDatabase()
    asyncio.run(db.create())
    yield db
    asyncio.run(db.drop())


def code_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic" / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()
