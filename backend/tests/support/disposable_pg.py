"""Throwaway PostgreSQL servers for runtime benchmarks (plan 4, stage 3).

Never tune a server you did not create: the default local test URL may share a
server with the persistent development database.

Local use: provision a server with initdb, optionally with the durability
candidate (``fsync``, ``synchronous_commit``, ``full_page_writes`` off), run a
command against it, record what it ran on, and remove it::

    python -m tests.support.disposable_pg run [--tuned] [--record=FILE] -- \\
        python -m pytest -n 4 tests/... --phase-report=...

The command gets ``BIOAF_TEST_DATABASE_URL`` (and ``BIOAF_DATABASE_URL``) for
the new server only. Effective settings are read from a fresh connection before
the command starts; a mismatch stops the run.

CI use: ``python -m tests.support.disposable_pg tune-ci-service`` tunes the
workflow's service container. It refuses outside GitHub Actions and refuses any
URL other than the service container's.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULTS = {"fsync": "on", "synchronous_commit": "on", "full_page_writes": "on"}
TUNED = {"fsync": "off", "synchronous_commit": "off", "full_page_writes": "off"}

CI_SERVICE = {"host": "localhost", "port": 5432, "database": "bioaf_test"}


class Refused(Exception):
    """The target is not a server this harness may tune."""


class SettingsMismatch(Exception):
    """The server's effective settings are not the ones the benchmark asked for."""


def find_pg_bin() -> Path | None:
    candidates = [os.environ.get("PG_BIN", "")]
    found = shutil.which("initdb")
    if found:
        candidates.append(str(Path(found).parent))
    candidates += sorted(glob.glob("/opt/homebrew/opt/postgresql@16/bin"))
    candidates += sorted(glob.glob("/usr/lib/postgresql/16/bin"))
    for c in candidates:
        if c and (Path(c) / "initdb").exists() and (Path(c) / "pg_ctl").exists():
            return Path(c)
    return None


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def read_settings(url: str) -> dict[str, str]:
    async def _read() -> dict[str, str]:
        import asyncpg

        conn = await asyncpg.connect(_plain(url), timeout=10)
        try:
            return {name: await conn.fetchval(f"SHOW {name}") for name in DEFAULTS}
        finally:
            await conn.close()

    return asyncio.run(_read())


def verify(actual: dict[str, str], expected: dict[str, str]) -> None:
    wrong = {k: actual.get(k) for k, v in expected.items() if actual.get(k) != v}
    if wrong:
        raise SettingsMismatch(f"expected {expected}, server reports {wrong}")


def _apply(url: str, settings: dict[str, str]) -> None:
    async def _go() -> None:
        import asyncpg

        conn = await asyncpg.connect(_plain(url), timeout=10)
        try:
            for name, value in settings.items():
                await conn.execute(f"ALTER SYSTEM SET {name} = '{value}'")
            await conn.execute("SELECT pg_reload_conf()")
        finally:
            await conn.close()

    asyncio.run(_go())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Server:
    data_dir: Path
    port: int
    pg_bin: Path
    user: str = "bioaf_test"
    database: str = "bioaf_test"

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.user}@127.0.0.1:{self.port}/{self.database}"

    def identity(self) -> dict:
        async def _read() -> dict:
            import asyncpg

            conn = await asyncpg.connect(_plain(self.url), timeout=10)
            try:
                sysid = await conn.fetchval("SELECT system_identifier::text FROM pg_control_system()")
                version = await conn.fetchval("SHOW server_version")
            finally:
                await conn.close()
            return {
                "system_identifier": sysid,
                "server_version": version,
                "port": self.port,
                "data_dir": str(self.data_dir),
            }

        return asyncio.run(_read())


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


@contextlib.contextmanager
def provision(tuned: bool = False, pg_bin: Path | None = None) -> Iterator[Server]:
    """A fresh PostgreSQL server owned by this process, removed on exit."""
    pg_bin = pg_bin or find_pg_bin()
    if pg_bin is None:
        raise RuntimeError("initdb not found; set PG_BIN to the PostgreSQL 16 bin directory")
    root = Path(tempfile.mkdtemp(prefix="bioaf-bench-pg-"))
    server = Server(data_dir=root / "data", port=_free_port(), pg_bin=pg_bin)
    started = False
    try:
        _run(
            [
                str(pg_bin / "initdb"),
                "-D",
                str(server.data_dir),
                "-U",
                server.user,
                "--auth=trust",
                "-E",
                "UTF8",
                "--locale=C",
            ]
        )
        # TCP only: a Unix socket path under a long temp dir can exceed the 103-byte limit.
        opts = f"-p {server.port} -k '' -c listen_addresses=127.0.0.1 -c max_connections=200"
        _run(
            [
                str(pg_bin / "pg_ctl"),
                "-D",
                str(server.data_dir),
                "-o",
                opts,
                "-l",
                str(root / "server.log"),
                "-w",
                "start",
            ]
        )
        started = True
        _run([str(pg_bin / "createdb"), "-h", "127.0.0.1", "-p", str(server.port), "-U", server.user, server.database])
        if tuned:
            _apply(server.url, TUNED)
        verify(read_settings(server.url), TUNED if tuned else DEFAULTS)
        yield server
    finally:
        if started:
            subprocess.run(
                [str(pg_bin / "pg_ctl"), "-D", str(server.data_dir), "-m", "immediate", "-w", "stop"],
                capture_output=True,
            )
        shutil.rmtree(root, ignore_errors=True)


def tune_ci_service(url: str) -> dict[str, str]:
    """Apply and verify the durability candidate on the CI service container only."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise Refused("tune-ci-service runs only inside GitHub Actions")
    parts = urlsplit(url)
    target = {"host": parts.hostname, "port": parts.port, "database": parts.path.lstrip("/")}
    if target != CI_SERVICE:
        raise Refused(f"{target} is not the CI service container {CI_SERVICE}")
    _apply(url, TUNED)
    settings = read_settings(url)
    verify(settings, TUNED)
    return settings


def main(argv: list[str]) -> int:
    if argv[:1] == ["tune-ci-service"]:
        url = os.environ["BIOAF_TEST_DATABASE_URL"]
        print(json.dumps(tune_ci_service(url)))
        return 0
    if argv[:1] != ["run"] or "--" not in argv:
        print("usage: disposable_pg run [--tuned] [--record=FILE] -- COMMAND... | tune-ci-service", file=sys.stderr)
        return 64
    options, command = argv[1 : argv.index("--")], argv[argv.index("--") + 1 :]
    tuned = "--tuned" in options
    record = next((Path(o.split("=", 1)[1]) for o in options if o.startswith("--record=")), None)
    info: dict = {"tuned": tuned, "command": command}
    with provision(tuned=tuned) as server:
        info["identity"] = server.identity()
        info["settings"] = read_settings(server.url)
        env = {**os.environ, "BIOAF_TEST_DATABASE_URL": server.url, "BIOAF_DATABASE_URL": server.url}
        info["returncode"] = subprocess.run(command, env=env).returncode
    info["removed"] = not Path(info["identity"]["data_dir"]).exists()
    if record:
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(info, indent=2))
    return int(info["returncode"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
