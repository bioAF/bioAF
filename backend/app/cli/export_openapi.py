"""CLI: export the main app's internal OpenAPI schema to a checked-in artifact.

The schema is the candidate authority for frontend transport types. It is built offline from
``create_app().openapi()``: the lifespan never runs, no database connection is opened and no
provider is called, so the command needs no credentials or infrastructure. Settings are isolated
before the app is imported: every inherited ``BIOAF_*`` variable is dropped, fixed synthetic
values are set, and the import runs from an empty temporary working directory, so no ``.env``
file (pydantic-settings reads one from the working directory) can break or change the output.
Production still serves neither docs nor this schema over HTTP; the mounted Integration API
keeps its own public schema and is not part of this one.

Normalization: ``info.version`` (the release number, bumped on every release) is replaced by
``NORMALIZED_VERSION``. Nothing else is removed. Keys are sorted and indented so the file
diffs cleanly; JSON object key order carries no contract meaning, and list order (``required``,
``enum``, parameters) is kept as emitted.

Usage:
    python -m app.cli.export_openapi            # rewrite contracts/internal-openapi.json
    python -m app.cli.export_openapi --check    # fail if the artifact is out of date; writes nothing
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
ARTIFACT = BACKEND / "contracts" / "internal-openapi.json"
NORMALIZED_VERSION = "internal"

# Not secrets: a well-formed placeholder key and an address nothing listens on. They only
# satisfy import-time settings validation.
SYNTHETIC_ENV = {
    "BIOAF_ENVIRONMENT": "production",
    "BIOAF_COMPUTE_MODE": "local",
    "BIOAF_ENCRYPTION_KEYS": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "BIOAF_DATABASE_URL": "postgresql+asyncpg://schema-export:unused@schema-export.invalid:5432/unused",
    "BIOAF_JWT_SECRET_KEY": "schema-export-placeholder-not-a-secret-0000000000",
}


def _isolate_settings(workdir: str) -> None:
    """Only the synthetic settings reach the app: no inherited BIOAF_* value and no .env file."""
    for key in [k for k in os.environ if k.startswith("BIOAF_")]:
        del os.environ[key]
    os.environ.update(SYNTHETIC_ENV)
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    os.chdir(workdir)


def build_schema() -> dict:
    """The main app's OpenAPI document with volatile metadata normalized."""
    from app.main import create_app

    schema = create_app().openapi()
    return {**schema, "info": {**schema["info"], "version": NORMALIZED_VERSION}}


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None, *, isolate: bool = False) -> int:
    """Export or check. ``isolate`` (the command line) pins settings before the app is imported;
    in-process callers that already imported the app leave it False."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare with the artifact; never write")
    parser.add_argument("--output", type=Path, default=ARTIFACT, help="artifact path (default: %(default)s)")
    args = parser.parse_args(argv)
    output: Path = args.output.resolve()

    if isolate:
        home = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="bioaf-schema-export-") as workdir:
            _isolate_settings(workdir)
            try:
                fresh = render(build_schema())
            finally:
                os.chdir(home)
    else:
        fresh = render(build_schema())
    if not args.check:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(fresh)
        return 0

    current = output.read_text() if output.exists() else ""
    if current == fresh:
        return 0
    diff = difflib.unified_diff(current.splitlines(), fresh.splitlines(), str(output), "fresh export", n=2, lineterm="")
    sys.stderr.write("\n".join(list(diff)[:200]) + "\n")
    sys.stderr.write(f"{output} is out of date with the API. Run `python -m app.cli.export_openapi` and commit it.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(isolate=True))
