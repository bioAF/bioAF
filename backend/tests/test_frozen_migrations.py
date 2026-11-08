"""Migrations 076 and 087 behave as published, without today's application code (plan 9, section 3).

A migration that imports application business logic changes meaning whenever that code changes, and
an install skipping releases runs the old migration with the new code. 087 imported its SQL from
app.services.notification_backfill; 076 imported the encryption service and the sensitive
platform-config key set. Both are frozen into the migrations now. 076 still receives the
installation's own encryption keys at runtime (through app.config, where every process gets them);
no key value is frozen.

These tests pin the frozen definitions to what the releases that introduced them published (read
from Git), and the historical matrix (test_historical_upgrades.py) proves the populated-data effect.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from tests.support.releases import REPO

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
MIGRATION_076 = VERSIONS / "076_encrypt_sensitive_columns.py"
MIGRATION_087 = VERSIONS / "087_backfill_notification_links.py"


def _published(ref: str, path: str) -> dict:
    """Top-level literal assignments of a file as published at ``ref``."""
    source = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{ref}:{path}"], check=True, capture_output=True, text=True
    ).stdout
    values = {}
    for node in ast.parse(source).body:
        target = (
            node.target
            if isinstance(node, ast.AnnAssign)
            else (node.targets[0] if isinstance(node, ast.Assign) else None)
        )
        if isinstance(target, ast.Name) and node.value is not None:
            value = node.value
            # frozenset({...}) / set({...}) literals, as the key set is written.
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) in {"frozenset", "set"} and value.args:
                value = value.args[0]
            try:
                values[target.id] = ast.literal_eval(value)
            except ValueError:
                pass
    return values


def _module(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _app_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app"):
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("app")}
    return found


def test_087_runs_the_sql_it_was_published_with_and_imports_no_application_code():
    published = _published("v2026.5.16", "backend/app/services/notification_backfill.py")
    assert _module(MIGRATION_087).BACKFILL_NOTIFICATION_LINKS_SQL == published["BACKFILL_NOTIFICATION_LINKS_SQL"]
    assert _app_imports(MIGRATION_087) == set()


def test_076_freezes_its_columns_and_sensitive_keys_and_only_reads_keys_from_config():
    published = _published("v0.13.0", "backend/alembic/versions/076_encrypt_sensitive_columns.py")
    keys = _published("v0.13.0", "backend/app/services/platform_config_service.py")
    migration = _module(MIGRATION_076)
    assert migration._ENCRYPTED_COLUMNS == published["_ENCRYPTED_COLUMNS"]
    assert migration.SENSITIVE_PLATFORM_CONFIG_KEYS == keys["SENSITIVE_PLATFORM_CONFIG_KEYS"]
    assert migration.FERNET_TOKEN_PREFIX == "gAAAA"
    assert _app_imports(MIGRATION_076) == {"app.config"}
