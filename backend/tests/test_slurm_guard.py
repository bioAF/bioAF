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

        s = Settings()
        assert s.compute_mode == "slurm"


@pytest.mark.asyncio
async def test_job_status_sync_skipped_when_not_slurm(monkeypatch):
    """The SLURM job status sync job exits immediately when compute_mode != 'slurm'."""
    import asyncio

    synced = []

    async def fake_sync(session):
        synced.append(True)

    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "kubernetes")

    # Reload settings with the monkeypatched env
    import importlib
    import sys

    for mod in list(sys.modules.keys()):
        if mod.startswith("app.config"):
            del sys.modules[mod]

    import app.config as cfg_mod

    importlib.reload(cfg_mod)

    from app.services import slurm_service
    from app.tasks.jobs import PERIODIC_JOBS
    from app.tasks.periodic import run_periodic

    monkeypatch.setattr(slurm_service.SlurmService, "sync_job_statuses", fake_sync)

    async def no_wait(delay, stopping):
        await asyncio.sleep(0)
        return False

    # Run the job -- it should return without calling sync
    job = next(j for j in PERIODIC_JOBS if j.role == "job-status-sync")
    task = asyncio.create_task(run_periodic(job, wait=no_wait))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert synced == [], "SlurmService.sync_job_statuses should not be called on non-SLURM deployments"
