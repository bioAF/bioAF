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


async def _run_status_sync_job(monkeypatch) -> list[bool]:
    """Start the job-status-sync worker as the scheduler does; return the SSH syncs it attempted."""
    import asyncio

    from app.services import slurm_service
    from app.tasks.jobs import PERIODIC_JOBS
    from app.tasks.periodic import run_periodic

    synced: list[bool] = []

    async def fake_sync(session):
        synced.append(True)

    monkeypatch.setattr(slurm_service.SlurmService, "sync_job_statuses", fake_sync)

    async def no_wait(delay, stopping):
        await asyncio.sleep(0)
        return False

    job = next(j for j in PERIODIC_JOBS if j.role == "job-status-sync")
    task = asyncio.create_task(run_periodic(job, wait=no_wait))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return synced


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["kubernetes", "slurm", "local"])
async def test_job_status_sync_skipped_when_not_slurm(monkeypatch, mode):
    """On a Kubernetes stack the SLURM status sync never runs, whatever BIOAF_COMPUTE_MODE says."""
    from app.adapters.registry import initialize_adapters_sync
    from app.config import settings
    from app.settings_contract import parse_compute_mode

    monkeypatch.setattr(settings, "compute_mode", parse_compute_mode(mode))
    initialize_adapters_sync("kubernetes")

    synced = await _run_status_sync_job(monkeypatch)
    assert synced == [], "SlurmService.sync_job_statuses should not be called on non-SLURM deployments"


@pytest.mark.asyncio
async def test_job_status_sync_skipped_for_a_simulated_slurm_stack(monkeypatch):
    from app.adapters.registry import initialize_adapters_sync
    from app.config import settings
    from app.settings_contract import ComputeMode

    monkeypatch.setattr(settings, "compute_mode", ComputeMode.LOCAL)
    initialize_adapters_sync("slurm")

    assert await _run_status_sync_job(monkeypatch) == []


@pytest.mark.asyncio
async def test_job_status_sync_runs_for_a_real_slurm_stack(monkeypatch):
    from app.adapters.registry import initialize_adapters_sync
    from app.config import settings
    from app.settings_contract import ComputeMode

    monkeypatch.setattr(settings, "compute_mode", ComputeMode.KUBERNETES)
    initialize_adapters_sync("slurm")

    assert await _run_status_sync_job(monkeypatch) != []
