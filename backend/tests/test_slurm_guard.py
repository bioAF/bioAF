import os
import pytest


def test_compute_mode_is_required_when_env_var_is_absent(tmp_path):
    """Settings has no compute-mode default: an absent BIOAF_COMPUTE_MODE is rejected, naming the setting.

    A fresh process in an empty directory, so neither the conftest pin nor a developer .env applies."""
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k.upper() != "BIOAF_COMPUTE_MODE"}
    env["PYTHONPATH"] = str(backend)
    proc = subprocess.run(
        [sys.executable, "-c", "from app.config import Settings; Settings()"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "BIOAF_COMPUTE_MODE" in proc.stderr


def test_compute_mode_reads_from_env():
    """Settings.compute_mode can be overridden to 'slurm' via env var."""
    with pytest.MonkeyPatch().context() as mp:
        mp.setenv("BIOAF_COMPUTE_MODE", "slurm")
        from app.config import Settings

        s = Settings()  # type: ignore[call-arg]  # read from the environment
        assert s.compute_mode == "slurm"


# Plan 03C (owner-approved 2026-11-24): SLURM is unsupported and its SSH connector, which connected with
# known_hosts=None, is retired with its periodic status poll. The SLURM status-poll tests were removed
# with it; stored SlurmJob history stays.
def test_no_slurm_ssh_connector_or_status_poll_remains():
    import importlib.util

    from app.tasks.jobs import PERIODIC_JOBS

    assert importlib.util.find_spec("app.services.slurm_service") is None
    assert "job-status-sync" not in {j.role for j in PERIODIC_JOBS}


def test_slurm_job_history_is_kept():
    from app.models.slurm_job import SlurmJob

    assert SlurmJob.__tablename__ == "slurm_jobs"
