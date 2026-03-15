import os
import pytest


def test_compute_mode_defaults_to_kubernetes():
    """Settings.compute_mode defaults to 'kubernetes' when env var is absent."""
    env = os.environ.copy()
    env.pop("BIOAF_COMPUTE_MODE", None)

    import sys

    # Remove cached config module so Settings re-reads env
    for mod in list(sys.modules.keys()):
        if mod.startswith("app.config"):
            del sys.modules[mod]

    with pytest.MonkeyPatch().context() as mp:
        mp.delenv("BIOAF_COMPUTE_MODE", raising=False)
        from app.config import Settings

        s = Settings()
        assert s.compute_mode == "kubernetes"


def test_compute_mode_reads_from_env():
    """Settings.compute_mode can be overridden to 'slurm' via env var."""
    with pytest.MonkeyPatch().context() as mp:
        mp.setenv("BIOAF_COMPUTE_MODE", "slurm")
        from app.config import Settings

        s = Settings()
        assert s.compute_mode == "slurm"


@pytest.mark.asyncio
async def test_job_status_sync_skipped_when_not_slurm(monkeypatch):
    """_job_status_sync_loop exits immediately when compute_mode != 'slurm'."""
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

    import app.main as main_mod

    importlib.reload(main_mod)

    from app.services import slurm_service

    monkeypatch.setattr(slurm_service.SlurmService, "sync_job_statuses", fake_sync)

    # Run the loop -- it should return without calling sync
    task = asyncio.create_task(main_mod._job_status_sync_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert synced == [], "SlurmService.sync_job_statuses should not be called on non-SLURM deployments"
