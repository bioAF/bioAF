"""``python -m app.cli.check_settings``: the settings preflight the entrypoint and ./bioaf run.

Exit 0 for valid settings, 78 (EX_CONFIG) for rejected settings, anything else when the check itself
could not run. Real settings parsing in a fresh interpreter, an empty working directory, an environment
built from nothing, and outbound connections refused: no database or provider is touched.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SECRET = "SENTINEL-jwt-secret-0123456789abcdef0123456789"
EX_CONFIG = 78

# Refuse every outbound connection, run the checker, then report which heavy clients it loaded.
RUNNER = """
import socket, sys
def _refuse(*a, **k):
    raise OSError("network blocked by test")
socket.socket.connect = _refuse
socket.create_connection = _refuse
{prelude}
from app.cli.check_settings import main
code = main()
loaded = sorted(m for m in ("app.database", "sqlalchemy.ext.asyncio", "google.cloud", "boto3", "kubernetes")
                if m in sys.modules)
print("LOADED=" + ",".join(loaded))
sys.exit(code)
"""


def _check(tmp_path: Path, env: dict[str, str], prelude: str = "") -> subprocess.CompletedProcess:
    base = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND), "BIOAF_JWT_SECRET_KEY": SECRET}
    return subprocess.run(
        [sys.executable, "-c", RUNNER.format(prelude=prelude)],
        cwd=tmp_path,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("env", [{}, {"BIOAF_COMPUTE_MODE": "typo"}, {"BIOAF_COMPUTE_MODE": "   "}])
def test_a_missing_or_invalid_mode_is_a_configuration_rejection(tmp_path, env):
    proc = _check(tmp_path, env)
    assert proc.returncode == EX_CONFIG
    assert "BIOAF_COMPUTE_MODE" in proc.stderr
    assert "local" in proc.stderr and "kubernetes" in proc.stderr
    assert "configuration" in proc.stderr
    # Not a database problem, and the shared checker prescribes no operation-specific recovery.
    for wrong in ("migrate", "maintenance", "database", "Traceback"):
        assert wrong not in proc.stderr
    assert SECRET not in proc.stderr + proc.stdout
    assert "LOADED=\n" in proc.stdout or proc.stdout.rstrip().endswith("LOADED=")


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("local", "local"),
        ("kubernetes", "kubernetes"),
        ("k8s", "kubernetes"),
        ("K8S", "kubernetes"),
        (" Kubernetes ", "kubernetes"),
        ("slurm", "slurm"),
    ],
)
def test_valid_settings_pass_and_report_the_canonical_mode(tmp_path, raw, canonical):
    proc = _check(tmp_path, {"BIOAF_COMPUTE_MODE": raw})
    assert proc.returncode == 0, proc.stderr
    assert f"compute mode {canonical}" in proc.stdout
    assert proc.stdout.rstrip().endswith("LOADED=")
    assert SECRET not in proc.stdout + proc.stderr


def test_another_rejected_setting_is_not_mislabeled_as_a_mode_failure(tmp_path):
    proc = _check(tmp_path, {"BIOAF_COMPUTE_MODE": "local", "BIOAF_JWT_EXPIRY_HOURS": "SENTINEL-not-a-number"})
    assert proc.returncode == EX_CONFIG
    assert "BIOAF_JWT_EXPIRY_HOURS" in proc.stderr
    assert "BIOAF_COMPUTE_MODE" not in proc.stderr
    assert "SENTINEL" not in proc.stderr


def test_a_check_that_cannot_run_is_distinguished_from_a_rejection(tmp_path):
    # The settings module cannot be imported at all: an inability to validate, not invalid settings.
    proc = _check(tmp_path, {"BIOAF_COMPUTE_MODE": "local"}, prelude="sys.modules['app.config'] = None")
    assert proc.returncode not in (0, EX_CONFIG)
    assert "could not" in proc.stderr
    assert "BIOAF_COMPUTE_MODE" not in proc.stderr
