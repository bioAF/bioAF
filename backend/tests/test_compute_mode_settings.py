"""Settings sources for ``BIOAF_COMPUTE_MODE``: required, normalized, and never echoing other inputs.

Each case runs in a fresh interpreter, in an empty working directory, with an environment built from
nothing, so a developer's ``.env``, the conftest pin, and cached imports cannot decide the result.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SECRET = "SENTINEL-jwt-secret-0123456789abcdef0123456789"

PROBE = """
import json, sys
try:
    import app.config as cfg
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
    sys.exit(0)
print(json.dumps({"mode": cfg.settings.compute_mode.value, "type": type(cfg.settings.compute_mode).__name__}))
"""


def _run(tmp_path: Path, env: dict[str, str], *, dotenv: str | None = None, code: str = PROBE):
    if dotenv is not None:
        (tmp_path / ".env").write_text(dotenv)
    base = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND), "BIOAF_JWT_SECRET_KEY": SECRET}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc


def _probe(tmp_path, env, **kwargs) -> dict:
    proc = _run(tmp_path, env, **kwargs)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_missing_mode_fails_at_import_naming_the_setting(tmp_path):
    result = _probe(tmp_path, {})
    assert result["error"] == "SettingsError"
    assert "BIOAF_COMPUTE_MODE" in result["message"]
    assert "local" in result["message"] and "kubernetes" in result["message"]


@pytest.mark.parametrize("raw", ["", "   ", "typo"])
def test_blank_or_unknown_mode_fails_at_import(tmp_path, raw):
    result = _probe(tmp_path, {"BIOAF_COMPUTE_MODE": raw})
    assert result["error"] == "SettingsError"
    assert "BIOAF_COMPUTE_MODE" in result["message"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("local", "local"),
        ("kubernetes", "kubernetes"),
        ("k8s", "kubernetes"),
        ("slurm", "slurm"),
        ("K8S", "kubernetes"),
        (" Kubernetes ", "kubernetes"),
        ("LOCAL", "local"),
        (" SLURM ", "slurm"),
    ],
)
def test_known_values_resolve_to_the_typed_canonical_mode(tmp_path, raw, expected):
    assert _probe(tmp_path, {"BIOAF_COMPUTE_MODE": raw}) == {"mode": expected, "type": "ComputeMode"}


def test_a_lowercase_environment_name_is_read(tmp_path):
    assert _probe(tmp_path, {"bioaf_compute_mode": "k8s"})["mode"] == "kubernetes"


def test_the_environment_overrides_the_working_directory_dotenv(tmp_path):
    result = _probe(tmp_path, {"BIOAF_COMPUTE_MODE": "local"}, dotenv="BIOAF_COMPUTE_MODE=kubernetes\n")
    assert result["mode"] == "local"


def test_the_dotenv_supplies_mode_when_the_environment_does_not(tmp_path):
    assert _probe(tmp_path, {}, dotenv="BIOAF_COMPUTE_MODE=kubernetes\n")["mode"] == "kubernetes"


def test_an_invalid_environment_value_does_not_fall_back_to_the_dotenv(tmp_path):
    result = _probe(tmp_path, {"BIOAF_COMPUTE_MODE": "typo"}, dotenv="BIOAF_COMPUTE_MODE=local\n")
    assert result["error"] == "SettingsError"


def test_explicit_settings_input_wins_over_the_environment(tmp_path):
    code = (
        "import json\n"
        "from app.config import Settings\n"
        "print(json.dumps({'mode': Settings(compute_mode='K8S').compute_mode.value}))\n"
    )
    assert _probe(tmp_path, {"BIOAF_COMPUTE_MODE": "local"}, code=code)["mode"] == "kubernetes"


@pytest.mark.parametrize("env", [{}, {"BIOAF_COMPUTE_MODE": "typo"}, {"BIOAF_COMPUTE_MODE": "  "}])
def test_rejection_never_exposes_other_supplied_settings(tmp_path, env):
    # Uncaught, as a server import would see it: the whole traceback must not carry the secret.
    proc = _run(tmp_path, env, code="import app.config")
    assert proc.returncode != 0
    assert "BIOAF_COMPUTE_MODE" in proc.stderr
    assert SECRET not in proc.stderr
    assert SECRET not in proc.stdout


@pytest.mark.parametrize("env", [{}, {"BIOAF_COMPUTE_MODE": "typo"}])
def test_rendered_validation_errors_hide_inputs(tmp_path, env):
    # Settings() constructed directly renders its ValidationError without input values.
    code = (
        "import os, sys\n"
        "os.environ['BIOAF_COMPUTE_MODE'] = 'local'\n"
        "from app.config import Settings\n"
        "os.environ.pop('BIOAF_COMPUTE_MODE')\n"
        + ("os.environ['BIOAF_COMPUTE_MODE'] = %r\n" % env["BIOAF_COMPUTE_MODE"] if env else "")
        + "try:\n"
        "    Settings()\n"
        "except Exception as exc:\n"
        "    print(str(exc)); print(repr(exc))\n"
    )
    proc = _run(tmp_path, {}, code=code)
    assert proc.returncode == 0, proc.stderr
    assert "compute_mode" in proc.stdout
    assert SECRET not in proc.stdout


@pytest.mark.parametrize(
    "hostile",
    [
        {"BIOAF_COMPUTE_MODE": "kubernetes"},
        {"bioaf_compute_mode": "k8s"},
        {"Bioaf_Compute_Mode": "typo", "BIOAF_COMPUTE_MODE": "slurm"},
    ],
)
def test_ordinary_pytest_pins_local_mode_whatever_the_shell_exports(tmp_path, hostile):
    code = (
        "import json\n"
        "import tests.conftest\n"
        "from app.config import settings\n"
        "print(json.dumps({'mode': settings.compute_mode.value}))\n"
    )
    env = {"PYTHONPATH": str(BACKEND), **hostile}
    assert _probe(tmp_path, env, code=code) == {"mode": "local"}
