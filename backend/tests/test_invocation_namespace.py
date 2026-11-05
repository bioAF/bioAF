"""Every pytest invocation owns its own schemas, records them, and removes them when it ends.

Two invocations against one database (two terminals, or a serial run beside a
parallel one) must not share or wipe each other's schemas, and an abandoned
invocation's schemas can be removed by name without touching anyone else's.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text

from tests.plugins import invocation
from tests.plugins.db_url import database_url, describe

BACKEND_DIR = Path(__file__).resolve().parent.parent
PLAIN_URL = database_url().replace("postgresql+asyncpg://", "postgresql://")


def _schemas_like(pattern: str) -> set[str]:
    async def _read() -> set[str]:
        conn = await asyncpg.connect(PLAIN_URL)
        try:
            rows = await conn.fetch("SELECT nspname FROM pg_namespace WHERE nspname LIKE $1", pattern)
            return {r["nspname"] for r in rows}
        finally:
            await conn.close()

    return asyncio.run(_read())


def _create_schema(name: str) -> None:
    async def _go() -> None:
        conn = await asyncpg.connect(PLAIN_URL)
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{name}"')
        finally:
            await conn.close()

    asyncio.run(_go())


# --- names ------------------------------------------------------------------


def test_schema_names_combine_prefix_invocation_and_worker():
    assert invocation.schema_name("test", "ab12cd", "gw3") == "test_ab12cd_gw3"
    assert invocation.schema_name("migration_check", "ab12cd", "master") == "migration_check_ab12cd_master"


@pytest.mark.parametrize("bad", ["", "AB", "a-b", 'x";drop', "a" * 17])
def test_invocation_ids_are_validated(bad):
    with pytest.raises(ValueError):
        invocation.validate_invocation_id(bad)


@pytest.mark.parametrize("worker", ["gw-1", "GW0", "x y"])
def test_worker_ids_are_validated(worker):
    with pytest.raises(ValueError):
        invocation.schema_name("test", "ab12", worker)


def test_quoted_names_are_double_quoted():
    assert invocation.quote("test_ab_gw0") == '"test_ab_gw0"'


# --- this run ---------------------------------------------------------------


def test_this_run_has_an_invocation_id():
    assert re.fullmatch(r"[a-z0-9]{1,16}", invocation.current_invocation_id())


@pytest.mark.asyncio
async def test_the_database_fixture_uses_this_invocations_schema_never_public(session, worker_id):
    current = (await session.execute(text("SELECT current_schema()"))).scalar_one()
    assert current == invocation.schema_name("test", invocation.current_invocation_id(), worker_id)
    assert current != "public"


def test_this_run_records_the_schemas_it_owns(db_engine, worker_id):
    manifest = invocation.manifest_path(invocation.current_invocation_id(), worker_id)
    data = json.loads(manifest.read_text())
    assert data["database"] == describe(database_url())
    assert invocation.schema_name("test", invocation.current_invocation_id(), worker_id) in data["schemas"]


# --- whole invocations, in subprocesses ---------------------------------------

PROBE_TEST = "tests/test_invocation_namespace.py::test_the_database_fixture_uses_this_invocations_schema_never_public"


def _run_pytest(*args: str, inv: str | None = None, extra_env: dict[str, str] | None = None):
    env = dict(os.environ)
    env.pop("BIOAF_TEST_INVOCATION_ID", None)
    if inv:
        env["BIOAF_TEST_INVOCATION_ID"] = inv
    env.update(extra_env or {})
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


@pytest.mark.parametrize("workers", ["0", "2"])
def test_a_finished_invocation_removes_its_schemas_and_manifest(workers, tmp_path):
    inv = f"done{workers}x"
    proc = _run_pytest("-n", workers, PROBE_TEST, inv=inv, extra_env={"BIOAF_TEST_MANIFEST_DIR": str(tmp_path)})
    out, _ = proc.communicate(timeout=180)
    assert proc.returncode == 0, out
    assert _schemas_like(f"%\\_{inv}\\_%") == set()
    assert list(tmp_path.glob(f"{inv}.*.json")) == []


def test_concurrent_invocations_do_not_collide(tmp_path):
    procs = [
        _run_pytest(
            "-n", workers, "tests/test_invocation_namespace.py", "-k", "fixture_uses or records",
            extra_env={"BIOAF_TEST_MANIFEST_DIR": str(tmp_path)},
        )
        for workers in ("0", "2", "2")
    ]  # fmt: skip
    for proc in procs:
        out, _ = proc.communicate(timeout=300)
        assert proc.returncode == 0, out


# --- cleanup of an abandoned invocation ---------------------------------------


def _abandon(manifest_dir: Path, inv: str, schemas: list[str], database: dict | None = None) -> None:
    for name in schemas:
        _create_schema(name)
    (manifest_dir / f"{inv}.gw0.json").write_text(
        json.dumps({"invocation_id": inv, "database": database or describe(database_url()), "schemas": schemas})
    )


def test_cleanup_removes_only_the_named_invocations_schemas(tmp_path):
    mine = ["test_abandon1_gw0", "migration_check_abandon1_gw0"]
    theirs = ["test_keepme1_gw0"]
    _abandon(tmp_path, "abandon1", mine)
    _abandon(tmp_path, "keepme1", theirs)
    # Same prefix, not recorded by anyone: a sweep would take it, a scoped cleanup must not.
    _create_schema("test_abandon1_gw9")
    try:
        dropped = invocation.cleanup("abandon1", directory=tmp_path)

        assert sorted(dropped) == sorted(mine)
        assert _schemas_like("%abandon1%") == {"test_abandon1_gw9"}
        assert _schemas_like("test_keepme1_gw0") == {"test_keepme1_gw0"}
        assert not list(tmp_path.glob("abandon1.*.json"))
        assert list(tmp_path.glob("keepme1.*.json"))
    finally:
        invocation.drop_schemas(["test_abandon1_gw9", "test_keepme1_gw0"])


def test_cleanup_refuses_a_manifest_recorded_against_another_database(tmp_path):
    _abandon(tmp_path, "elsewhere1", ["test_elsewhere1_gw0"], database={**describe(database_url()), "port": 1})
    try:
        with pytest.raises(invocation.CleanupRefused):
            invocation.cleanup("elsewhere1", directory=tmp_path)
        assert _schemas_like("test_elsewhere1_gw0") == {"test_elsewhere1_gw0"}
    finally:
        invocation.drop_schemas(["test_elsewhere1_gw0"])


def test_cleanup_of_an_unknown_invocation_is_refused(tmp_path):
    with pytest.raises(invocation.CleanupRefused):
        invocation.cleanup("neverseen1", directory=tmp_path)


def test_cleanup_refuses_names_outside_the_invocation(tmp_path):
    """A tampered manifest cannot be used to drop someone else's schema."""
    _create_schema("test_victim1_gw0")
    (tmp_path / "evil1.gw0.json").write_text(
        json.dumps({"invocation_id": "evil1", "database": describe(database_url()), "schemas": ["test_victim1_gw0"]})
    )
    try:
        with pytest.raises(invocation.CleanupRefused):
            invocation.cleanup("evil1", directory=tmp_path)
        assert _schemas_like("test_victim1_gw0") == {"test_victim1_gw0"}
    finally:
        invocation.drop_schemas(["test_victim1_gw0"])


SLEEPING_PROBE = """
import asyncio
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_holds_its_schema(session):
    await session.execute(text("SELECT 1"))
    print("SCHEMA-READY", flush=True)
    await asyncio.sleep(120)
"""


@pytest.mark.parametrize("workers", ["0", "2"])
def test_an_interrupted_invocation_still_removes_its_schemas(workers, tmp_path):
    import signal
    import time
    import uuid as _uuid

    inv = f"intr{workers}x"
    probe = BACKEND_DIR / "tests" / f"_sigint_probe_{_uuid.uuid4().hex[:8]}.py"
    probe.write_text(SLEEPING_PROBE)
    try:
        proc = _run_pytest(
            "-n", workers, "-s", str(probe.relative_to(BACKEND_DIR)),
            inv=inv, extra_env={"BIOAF_TEST_MANIFEST_DIR": str(tmp_path)},
        )  # fmt: skip
        deadline = time.monotonic() + 60
        while not _schemas_like(f"test\\_{inv}\\_%") and time.monotonic() < deadline:
            time.sleep(0.5)
        assert _schemas_like(f"test\\_{inv}\\_%"), "the probe never created its schema"
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=120)
    finally:
        probe.unlink()

    assert _schemas_like(f"%\\_{inv}\\_%") == set()
    assert list(tmp_path.glob(f"{inv}.*.json")) == []
