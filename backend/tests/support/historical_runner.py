"""Build a released bioAF schema exactly as that release built it (plan 9, section 5).

Historical ``env.py`` files are not a portable harness (they import today's application modules and
the earliest one ignores an injected connection), and migration bodies import application code (076:
configuration, encryption and the sensitive platform-config keys; 087: its backfill SQL). So for a
release commit this:

1. extracts that commit's ``backend/alembic/versions``, ``backend/app`` and ``backend/pyproject.toml``
   (its ``app.config`` reads the version from it) from local Git objects into
   an owned directory, with a test-owned ``env.py`` that needs an explicitly supplied URL, imports no
   ``app`` module itself, and never falls back to application settings;
2. runs ``alembic upgrade <head>`` in an isolated interpreter (``python -I``: no cwd, no PYTHONPATH, no
   user site) whose only ``app`` package is the extracted one, so modules already imported by the
   suite cannot stand in for the release's; and
3. reports which ``app`` modules the migrations imported and the shared third-party toolchain.

The toolchain is this virtualenv's (one pinned migration-test toolchain). ``BIOAF_DATABASE_URL`` is
set to an unreachable address so any accidental use of application settings fails loudly.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tests.support.releases import REPO

UNREACHABLE_URL = "postgresql+asyncpg://refused:refused@127.0.0.1:9/historical_env_must_not_use_this"

# Disposable keys for the subprocess only. 076 encrypts with the installation's key at runtime; the
# matrix supplies its own so ciphertext can be checked after the candidate takes over.
TEST_ENCRYPTION_KEY = "eBw4UgwPgLbUxEFsTZ74cefV1Vemr73qHGhZl0rAE8k="
TEST_JWT_SECRET = "historical-upgrade-matrix-not-a-real-secret-0123456789"

ENV_PY = '''"""Test-owned Alembic environment for building a released schema. Imports no app module."""
import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

url = context.get_x_argument(as_dictionary=True).get("url")
if not url:
    raise SystemExit("historical env.py needs -x url=<disposable database>; it never reads app settings")


def _run(sync_conn):
    context.configure(connection=sync_conn, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def _main():
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


asyncio.run(_main())
'''

ALEMBIC_INI = """[alembic]
script_location = %(here)s/alembic
"""

BOOTSTRAP = """
import json, sys
root = sys.argv[1]
sys.path.insert(0, root)
from alembic.config import main
main(argv=["-c", root + "/alembic.ini", "-x", "url=" + sys.argv[2], "upgrade", sys.argv[3]])
import alembic, sqlalchemy, cryptography, asyncpg
print("HISTORICAL_MANIFEST " + json.dumps({
    "app_modules": sorted(m for m in sys.modules if m == "app" or m.startswith("app.")),
    "app_files": sorted({getattr(sys.modules[m], "__file__", None) or "" for m in sys.modules
                         if m.startswith("app.")}),
    "python": sys.version.split()[0],
    "alembic": alembic.__version__, "sqlalchemy": sqlalchemy.__version__,
    "cryptography": cryptography.__version__, "asyncpg": asyncpg.__version__,
}))
"""


class HistoricalBuildFailed(Exception):
    pass


@dataclass
class Manifest:
    commit: str
    head: str
    app_modules: list[str] = field(default_factory=list)
    toolchain: dict[str, str] = field(default_factory=dict)
    source_root: str = ""


_EXTRACTED: dict[str, Path] = {}


def extract(commit: str) -> Path:
    """The release's migrations and application package, plus the test-owned env.py, in an owned dir."""
    if commit in _EXTRACTED:
        return _EXTRACTED[commit]
    root = Path(tempfile.mkdtemp(prefix=f"bioaf-release-{commit[:10]}-"))
    archive = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "archive",
            "--format=tar",
            commit,
            "backend/alembic/versions",
            "backend/app",
            "backend/pyproject.toml",
        ],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter="data")
    staged = root / "backend"
    (staged / "alembic" / "env.py").write_text(ENV_PY)
    (staged / "alembic" / "script.py.mako").write_text("")
    (staged / "alembic.ini").write_text(ALEMBIC_INI)
    _EXTRACTED[commit] = staged
    return staged


def build(commit: str, head: str, url: str) -> Manifest:
    """Run ``commit``'s migrations up to ``head`` against ``url`` (an empty disposable database)."""
    root = extract(commit)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "BIOAF_DATABASE_URL": UNREACHABLE_URL,
        "BIOAF_ENCRYPTION_KEYS": TEST_ENCRYPTION_KEY,
        "BIOAF_JWT_SECRET_KEY": TEST_JWT_SECRET,
        "BIOAF_ENVIRONMENT": "test",
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-c", BOOTSTRAP, str(root), url, head],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise HistoricalBuildFailed(f"{commit[:10]} -> {head} exited {proc.returncode}:\n{proc.stderr[-4000:]}")
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("HISTORICAL_MANIFEST "))
    data = json.loads(line.split(" ", 1)[1])
    leaked = [f for f in data["app_files"] if f and not f.startswith(str(root))]
    if leaked:
        raise HistoricalBuildFailed(f"app modules loaded from outside the release: {leaked}")
    return Manifest(
        commit=commit,
        head=head,
        app_modules=data["app_modules"],
        toolchain={k: data[k] for k in ("python", "alembic", "sqlalchemy", "cryptography", "asyncpg")},
        source_root=str(root),
    )
