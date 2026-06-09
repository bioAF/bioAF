"""Architecture guard: no raw SQL against the platform_config table.

All platform_config access must go through PlatformConfigService (get/get_many/
set), which is the only place the sensitive-key encrypt/decrypt boundary is
enforced. Raw SQL bypasses that boundary and duplicates query logic across the
codebase, so this test fails if any module other than the allow-listed owners
runs raw platform_config SQL.

If you are adding a new platform_config key, route it through the service. If you
genuinely need raw access (you almost certainly do not), add the file to
ALLOWED_RAW below with a comment explaining why.
"""

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Relative-to-app paths permitted to run raw platform_config SQL.
ALLOWED_RAW = {
    # The service IS the single owner of the table.
    "platform/platform_config_service.py",
    # Re-encrypts existing rows by id at the ciphertext level during key
    # rotation; routing through the service would double-encrypt.
    "cli/rotate_encryption_keys.py",
}

# Matches a raw SQL operation naming the table: FROM/INTO/UPDATE/DELETE FROM.
# Case-sensitive on purpose: SQL keywords are uppercase in this codebase, so this
# ignores prose like "read config from platform_config" in comments/docstrings.
_RAW_SQL = re.compile(r"(?:FROM|INTO|UPDATE|DELETE\s+FROM)\s+platform_config\b")


def _offending_files() -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in APP_ROOT.rglob("*.py"):
        rel = path.relative_to(APP_ROOT).as_posix()
        if rel in ALLOWED_RAW:
            continue
        hits = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if _RAW_SQL.search(line)]
        if hits:
            offenders[rel] = hits
    return offenders


def test_no_raw_platform_config_sql_outside_service():
    offenders = _offending_files()
    assert not offenders, (
        "Raw platform_config SQL found outside PlatformConfigService. Route these "
        "through PlatformConfigService.get/get_many/set:\n"
        + "\n".join(f"  {f}: {len(lines)} occurrence(s)" for f, lines in sorted(offenders.items()))
    )
