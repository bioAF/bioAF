"""One schema namespace per pytest invocation, recorded so it can be removed.

Every invocation gets an id (``BIOAF_TEST_INVOCATION_ID``, or a random one),
shared by all of its xdist workers. Schemas are named
``<prefix>_<invocation>_<worker>`` (``test_3f9a..._gw0``, ``test_..._master``),
so a serial run and a parallel run, or two terminals, never share a schema and
never wipe ``public``.

Before a schema is created its name is written to a manifest,
``<manifest dir>/<invocation>.<worker>.json`` (``BIOAF_TEST_MANIFEST_DIR``, by
default ``$TMPDIR/bioaf-test-invocations``), with the database it lives in. When
a process's session ends normally, or is interrupted with Ctrl-C, it drops its
schemas and deletes its manifest. A hard kill leaves both behind; remove them
with::

    python -m tests.plugins.invocation list
    python -m tests.plugins.invocation cleanup <invocation-id>

Cleanup drops only the names that invocation recorded, only in the database it
recorded, and only names that belong to it. Nothing sweeps by prefix, age or
PID.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.plugins.db_url import database_url, describe

INVOCATION_ENV = "BIOAF_TEST_INVOCATION_ID"
MANIFEST_DIR_ENV = "BIOAF_TEST_MANIFEST_DIR"

_ID_RE = re.compile(r"[a-z0-9]{1,16}")
_WORKER_RE = re.compile(r"master|gw[0-9]{1,4}")
_PREFIX_RE = re.compile(r"[a-z][a-z_]{0,20}")
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,62}")

_STATE: dict[str, Any] = {"owned": []}


class CleanupRefused(Exception):
    """A cleanup request that does not match what an invocation recorded."""


def validate_invocation_id(value: str) -> str:
    if not _ID_RE.fullmatch(value or ""):
        raise ValueError(f"invocation id must be 1-16 lowercase letters or digits, got {value!r}")
    return value


def schema_name(prefix: str, invocation_id: str, worker: str) -> str:
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(f"bad schema prefix {prefix!r}")
    if not _WORKER_RE.fullmatch(worker):
        raise ValueError(f"bad worker id {worker!r}")
    return f"{prefix}_{validate_invocation_id(invocation_id)}_{worker}"


def quote(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"refusing to quote identifier {name!r}")
    return f'"{name}"'


def manifest_dir() -> Path:
    configured = os.environ.get(MANIFEST_DIR_ENV)
    return Path(configured) if configured else Path(tempfile.gettempdir()) / "bioaf-test-invocations"


def manifest_path(invocation_id: str, worker: str, directory: Path | None = None) -> Path:
    if not _WORKER_RE.fullmatch(worker):
        raise ValueError(f"bad worker id {worker!r}")
    return (directory or manifest_dir()) / f"{validate_invocation_id(invocation_id)}.{worker}.json"


def current_invocation_id() -> str:
    try:
        return _STATE["invocation_id"]
    except KeyError:
        raise RuntimeError("the invocation plugin is not configured in this process") from None


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def claim_schema(prefix: str, worker: str) -> str:
    """The schema this process should use for ``prefix``, recorded before anyone creates it."""
    inv = current_invocation_id()
    name = schema_name(prefix, inv, worker)
    owned: list[str] = _STATE["owned"]
    if name not in owned:
        owned.append(name)
        _STATE["worker"] = worker
        _write_manifest(
            manifest_path(inv, worker),
            {
                "invocation_id": inv,
                "worker": worker,
                "pid": os.getpid(),
                "database": describe(database_url()),
                "schemas": list(owned),
            },
        )
    return name


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def drop_schemas(names: list[str], url: str | None = None) -> None:
    quoted = [quote(n) for n in names]
    if not quoted:
        return

    async def _drop() -> None:
        import asyncpg

        conn = await asyncpg.connect(_plain(url or database_url()), timeout=10)
        try:
            for name in quoted:
                await conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
        finally:
            await conn.close()

    asyncio.run(_drop())


def cleanup(invocation_id: str, directory: Path | None = None, url: str | None = None) -> list[str]:
    """Drop the schemas one abandoned invocation recorded, and its manifests.

    Every manifest is checked before anything is dropped: it must name this
    invocation, the database ``url`` points at, and only schemas of this
    invocation.
    """
    inv = validate_invocation_id(invocation_id)
    directory = directory or manifest_dir()
    files = sorted(directory.glob(f"{inv}.*.json"))
    if not files:
        raise CleanupRefused(f"no manifest for invocation {inv} in {directory}")
    target = describe(url or database_url())
    owned = re.compile(rf"[a-z][a-z_]{{0,20}}_{inv}_(master|gw[0-9]{{1,4}})")
    names: list[str] = []
    for path in files:
        data = json.loads(path.read_text())
        if data.get("invocation_id") != inv:
            raise CleanupRefused(f"{path} belongs to invocation {data.get('invocation_id')!r}")
        if data.get("database") != target:
            raise CleanupRefused(f"{path} was recorded against {data.get('database')}, not {target}")
        for name in data.get("schemas", []):
            if not owned.fullmatch(name):
                raise CleanupRefused(f"{path} lists {name!r}, which is not a schema of invocation {inv}")
            names.append(name)
    drop_schemas(names, url)
    for path in files:
        path.unlink()
    return names


# --- pytest hooks -------------------------------------------------------------


class _XdistHooks:
    def pytest_configure_node(self, node: Any) -> None:
        node.workerinput["bioaf_invocation_id"] = _STATE["invocation_id"]


def pytest_configure(config: pytest.Config) -> None:
    workerinput = getattr(config, "workerinput", None)
    if workerinput is not None:
        inv = workerinput["bioaf_invocation_id"]
    else:
        inv = os.environ.get(INVOCATION_ENV) or uuid.uuid4().hex[:12]
    _STATE.update(invocation_id=validate_invocation_id(inv), owned=[])
    if workerinput is None and config.pluginmanager.hasplugin("xdist"):
        config.pluginmanager.register(_XdistHooks(), "bioaf_invocation_xdist")


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Remove this process's schemas; keep the manifest if that fails, so cleanup can finish it."""
    owned: list[str] = _STATE.get("owned") or []
    worker = _STATE.get("worker")
    if not owned or worker is None:
        return
    try:
        drop_schemas(owned)
    except Exception as exc:  # leave the manifest for the cleanup command
        sys.stderr.write(f"bioaf: could not drop test schemas {owned}: {exc}\n")
        return
    manifest_path(_STATE["invocation_id"], worker).unlink(missing_ok=True)
    _STATE["owned"] = []


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        for path in sorted(manifest_dir().glob("*.json")):
            data = json.loads(path.read_text())
            print(f"{data['invocation_id']}  {data.get('worker')}  pid={data.get('pid')}  {data['schemas']}")
        return 0
    if len(argv) == 2 and argv[0] == "cleanup":
        try:
            dropped = cleanup(argv[1])
        except CleanupRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print("dropped: " + ", ".join(dropped))
        return 0
    print("usage: python -m tests.plugins.invocation list | cleanup <invocation-id>", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
