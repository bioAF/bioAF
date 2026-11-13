"""The registry publishes the compute stack it actually initialized, and nothing else.

Consumers that must agree with the active adapters (the SLURM status worker) read this, not a fresh
database value or the environment mode. It exists only after a successful initialization.
"""

import pytest

from app.adapters.registry import (
    get_active_compute_stack,
    initialize_adapters,
    initialize_adapters_sync,
    reset_registry,
)
from app.config import settings
from app.platform.platform_config_service import PlatformConfigService
from app.settings_contract import ComputeMode


def test_no_stack_before_initialization():
    reset_registry()
    assert get_active_compute_stack() is None


@pytest.mark.parametrize("stack", ["kubernetes", "slurm"])
def test_sync_initialization_publishes_the_stack_and_reset_clears_it(stack):
    initialize_adapters_sync(stack)
    assert get_active_compute_stack() == stack
    reset_registry()
    assert get_active_compute_stack() is None


@pytest.mark.parametrize("mode", list(ComputeMode))
def test_the_environment_mode_never_selects_the_stack(mode, monkeypatch):
    monkeypatch.setattr(settings, "compute_mode", mode)
    initialize_adapters_sync("kubernetes")
    assert get_active_compute_stack() == "kubernetes"


@pytest.mark.asyncio
async def test_async_initialization_publishes_the_database_stack(session):
    reset_registry()
    await initialize_adapters(session)
    assert get_active_compute_stack() == "kubernetes"

    await PlatformConfigService.set(session, "compute_stack", "slurm")
    await session.flush()
    # Restart-bound: a persisted change is not the active stack until the registry initializes again.
    assert get_active_compute_stack() == "kubernetes"
    await initialize_adapters(session)
    assert get_active_compute_stack() == "slurm"


@pytest.mark.asyncio
async def test_a_failed_initialization_exposes_no_stack(session):
    initialize_adapters_sync("kubernetes")
    await PlatformConfigService.set(session, "compute_stack", "mainframe")
    await session.flush()
    with pytest.raises(Exception):
        await initialize_adapters(session)
    assert get_active_compute_stack() is None


@pytest.mark.asyncio
async def test_a_failure_after_the_stack_is_chosen_exposes_no_stack(session, monkeypatch):
    reset_registry()

    async def unreachable(self, force=False):
        raise RuntimeError("cluster config unreadable")

    from app.adapters.compute.kubernetes import KubernetesComputeProvider

    monkeypatch.setattr(KubernetesComputeProvider, "load_cluster_config", unreachable)
    with pytest.raises(RuntimeError):
        await initialize_adapters(session)
    assert get_active_compute_stack() is None


def test_a_failed_sync_reinitialization_exposes_no_stack(monkeypatch):
    from app.exceptions import ValidationError
    from app.tasks.jobs import PERIODIC_JOBS

    monkeypatch.setattr(settings, "compute_mode", ComputeMode.KUBERNETES)
    initialize_adapters_sync("slurm")
    with pytest.raises(ValidationError):
        initialize_adapters_sync("kubernetes", work_node_backend="mainframe")
    assert get_active_compute_stack() is None
    job = next(j for j in PERIODIC_JOBS if j.role == "job-status-sync")
    assert job.enabled is not None and not job.enabled()
