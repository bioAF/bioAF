"""The main app's internal OpenAPI schema is exported offline to a checked-in artifact.

``python -m app.cli.export_openapi`` builds the app and serializes ``openapi()`` with fixed
synthetic settings. It never enters the lifespan, opens a database connection or reaches the
network, so it runs anywhere without credentials. ``--check`` compares a fresh export with the
artifact and fails on any difference without rewriting it. The mounted Integration API keeps
its own public schema and is not part of this one; production still serves neither docs nor
the main schema over HTTP (test_openapi_exposure.py).
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app.cli import export_openapi

BACKEND = Path(__file__).resolve().parents[1]
ARTIFACT = BACKEND / "contracts" / "internal-openapi.json"


def _clean_env() -> dict:
    """An environment with no bioAF settings and no cloud credentials."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BIOAF_", "GOOGLE_", "AWS_", "CLOUDSDK_"))}
    env["GOOGLE_APPLICATION_CREDENTIALS"] = "/nonexistent"
    return env


def _export_in_subprocess(tmp_path: Path, name: str) -> bytes:
    out = tmp_path / name
    subprocess.run(
        [sys.executable, "-m", "app.cli.export_openapi", "--output", str(out)],
        cwd=BACKEND,
        env=_clean_env(),
        check=True,
        capture_output=True,
        timeout=120,
    )
    return out.read_bytes()


def test_the_artifact_is_the_default_output_and_is_not_served():
    assert export_openapi.ARTIFACT == ARTIFACT
    # Outside app/ (the only backend source the image copies) and outside the frontend.
    assert "app" not in ARTIFACT.relative_to(BACKEND).parts
    assert "frontend" not in ARTIFACT.parts


def test_two_clean_exports_are_byte_identical_and_match_the_checked_in_artifact(tmp_path):
    first = _export_in_subprocess(tmp_path, "a.json")
    second = _export_in_subprocess(tmp_path, "b.json")
    assert first == second
    assert first == ARTIFACT.read_bytes(), "run `python -m app.cli.export_openapi` and commit the artifact"


# Settings that would break or change an export if they leaked in: a non-integer port fails
# Settings validation, the others would change settings the export pins.
_HOSTILE = {
    "BIOAF_SMTP_PORT": "not-a-port",
    "BIOAF_ENVIRONMENT": "development",
    "BIOAF_ENCRYPTION_KEYS": "not-a-fernet-key",
    "BIOAF_JWT_EXPIRY_HOURS": "soon",
    "BIOAF_DEBUG": "maybe",
}


def _run_cli(cwd: Path, env: dict, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "app.cli.export_openapi", "--output", str(out)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_inherited_bioaf_settings_cannot_break_or_change_the_export(tmp_path):
    out = tmp_path / "schema.json"
    result = _run_cli(BACKEND, {**_clean_env(), **_HOSTILE}, out)
    assert result.returncode == 0, result.stderr[-2000:]
    assert out.read_bytes() == ARTIFACT.read_bytes()


def test_a_dotenv_in_the_working_directory_cannot_break_or_change_the_export(tmp_path):
    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    (workdir / ".env").write_text("".join(f"{k}={v}\n" for k, v in _HOSTILE.items()))
    out = tmp_path / "schema.json"
    result = _run_cli(workdir, {**_clean_env(), "PYTHONPATH": str(BACKEND)}, out)
    assert result.returncode == 0, result.stderr[-2000:]
    assert out.read_bytes() == ARTIFACT.read_bytes()


def test_a_relative_output_path_is_relative_to_where_the_command_ran(tmp_path):
    result = _run_cli(tmp_path, {**_clean_env(), "PYTHONPATH": str(BACKEND)}, Path("nested/schema.json"))
    assert result.returncode == 0, result.stderr[-2000:]
    assert (tmp_path / "nested" / "schema.json").read_bytes() == ARTIFACT.read_bytes()


def test_the_export_does_not_start_the_app_open_a_connection_or_use_the_network(monkeypatch):
    import app.main as main_module
    from sqlalchemy.ext.asyncio import AsyncEngine

    def _refuse(*args, **kwargs):
        raise AssertionError("the schema export must not do this")

    entered = []

    def _lifespan(app):
        entered.append(app)
        raise AssertionError("lifespan entered")

    monkeypatch.setattr(main_module, "lifespan", _lifespan)
    monkeypatch.setattr(AsyncEngine, "connect", _refuse)
    monkeypatch.setattr(AsyncEngine, "begin", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)

    schema = export_openapi.build_schema()
    assert schema["paths"]
    assert entered == []


def test_volatile_metadata_is_normalized_and_nothing_else_is_dropped():
    from app.main import create_app

    raw = create_app().openapi()
    schema = export_openapi.build_schema()
    assert schema["info"]["version"] == export_openapi.NORMALIZED_VERSION
    assert {**schema, "info": raw["info"]} == raw


def test_the_internal_schema_leaves_the_integration_api_out():
    schema = json.loads(ARTIFACT.read_text())
    assert not [path for path in schema["paths"] if path.startswith("/api/v1/integrations")]
    assert "/api/pipeline-runs/{run_id}/reviews" in schema["paths"]


def test_the_artifact_holds_no_synthetic_or_environment_secret():
    text = ARTIFACT.read_text()
    for key in ("BIOAF_ENCRYPTION_KEYS", "BIOAF_DATABASE_URL", "BIOAF_JWT_SECRET_KEY"):
        assert export_openapi.SYNTHETIC_ENV[key] not in text
    for key in ("BIOAF_ENCRYPTION_KEYS", "BIOAF_JWT_SECRET_KEY"):
        if os.environ.get(key):
            assert os.environ[key] not in text


def test_check_mode_passes_on_a_match_and_fails_on_drift_without_rewriting(tmp_path, capsys):
    current = tmp_path / "current.json"
    current.write_text(export_openapi.render(export_openapi.build_schema()))
    assert export_openapi.main(["--check", "--output", str(current)]) == 0

    drifted = json.loads(current.read_text())
    drifted["components"]["schemas"]["UserSummary"]["required"].remove("email")
    stale = tmp_path / "stale.json"
    stale.write_text(export_openapi.render(drifted))
    before = stale.read_bytes()
    assert export_openapi.main(["--check", "--output", str(stale)]) == 1
    assert stale.read_bytes() == before
    assert "out of date" in capsys.readouterr().err

    missing = tmp_path / "missing.json"
    assert export_openapi.main(["--check", "--output", str(missing)]) == 1
    assert not missing.exists()


@pytest.mark.parametrize("change", ["field_added", "required_dropped", "nullability_changed"])
def test_a_schema_change_is_drift(change):
    schema = export_openapi.build_schema()
    reviewer = schema["components"]["schemas"]["PipelineRunReviewResponse"]
    if change == "field_added":
        reviewer["properties"]["extra"] = {"type": "string", "title": "Extra"}
    elif change == "required_dropped":
        reviewer["required"].remove("verdict")
    else:
        reviewer["properties"]["notes"] = {"type": "string", "title": "Notes"}
    assert export_openapi.render(schema) != ARTIFACT.read_text()
