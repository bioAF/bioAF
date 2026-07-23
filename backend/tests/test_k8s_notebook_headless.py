"""K8s notebook adapter: headless (non-interactive) execution path (lit_validation Level-3).

A headless run executes an injected, pre-parameterized notebook with nbconvert and exits; it
creates no Service and its completion is read off the notebook container's terminated state (the
gcs-sync sidecar keeps the pod phase Running). K8s API is mocked.
"""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.exceptions import ValidationError

_NB = {
    "cells": [{"cell_type": "code", "metadata": {"tags": ["parameters"]}, "source": ["x = 1\n"]}],
    "metadata": {"kernelspec": {"name": "ir"}},
    "nbformat": 4,
    "nbformat_minor": 5,
}


@pytest.fixture
def adapter():
    p = KubernetesNotebookProvider()
    p._mode = "k8s"
    p._namespace_ready = False
    p._api_client = MagicMock()
    p._client_created_at = time.monotonic()
    return p


@pytest.fixture
def mock_k8s_clients():
    mock_core = MagicMock()
    mock_rbac = MagicMock()
    mock_core.read_namespace.return_value = MagicMock()
    mock_core.create_namespaced_pod.return_value = MagicMock()
    return mock_core, mock_rbac


def _headless_spec(**kw):
    s = {
        "session_type": "headless",
        "session_id": 55,
        "user_id": 7,
        "resource_profile": "medium",
        "cpu_cores": 4,
        "memory_gb": 16,
        "experiment_id": None,
        "notebook_json": _NB,
        "notebook_name": "de.ipynb",
    }
    s.update(kw)
    return s


def _pod_with_notebook_state(*, terminated_exit=None, running=False, label="headless"):
    pod = MagicMock()
    pod.metadata.labels = {"bioaf.io/type": label}
    pod.status.phase = "Running"
    nb = MagicMock()
    nb.name = "notebook"
    if terminated_exit is not None:
        term = MagicMock()
        term.exit_code = terminated_exit
        nb.state.terminated = term
        nb.state.running = None
    elif running:
        nb.state.terminated = None
        nb.state.running = MagicMock()
    else:
        nb.state.terminated = None
        nb.state.running = None
    pod.status.container_statuses = [nb]
    return pod


def test_headless_manifest_runs_nbconvert(adapter):
    m = adapter._build_pod_manifest(_headless_spec(), has_gcs_secret=False)
    assert m["metadata"]["labels"]["bioaf.io/type"] == "headless"
    nb = next(c for c in m["spec"]["containers"] if c["name"] == "notebook")
    cmd = " ".join(nb["command"])
    assert "nbconvert" in cmd and "--execute" in cmd and "base64 -d" in cmd


def test_headless_manifest_requires_notebook_json(adapter):
    spec = _headless_spec()
    del spec["notebook_json"]
    with pytest.raises(ValidationError):
        adapter._build_pod_manifest(spec, has_gcs_secret=False)


@pytest.mark.asyncio
async def test_headless_launch_skips_service(adapter, mock_k8s_clients):
    mock_core, mock_rbac = mock_k8s_clients
    adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
    adapter._get_k8s_rbac_client = MagicMock(return_value=mock_rbac)
    adapter._poll_session_ready = AsyncMock()

    await adapter._k8s_launch_session(_headless_spec())

    mock_core.create_namespaced_pod.assert_called_once()
    mock_core.create_namespaced_service.assert_not_called()


@pytest.mark.asyncio
async def test_headless_status_completed_from_container_exit(adapter):
    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = _pod_with_notebook_state(terminated_exit=0)
    adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
    r = await adapter._k8s_get_session_status(
        session_id="bioaf-notebook-55", pod_name="bioaf-notebook-55", namespace="bioaf-notebooks"
    )
    assert r["status"] == "stopped"


@pytest.mark.asyncio
async def test_headless_status_failed_from_container_exit(adapter):
    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = _pod_with_notebook_state(terminated_exit=1)
    adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
    r = await adapter._k8s_get_session_status(pod_name="bioaf-notebook-55", namespace="bioaf-notebooks")
    assert r["status"] == "error"


@pytest.mark.asyncio
async def test_headless_status_running_from_container_state(adapter):
    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = _pod_with_notebook_state(running=True)
    adapter._get_k8s_core_client = MagicMock(return_value=mock_core)
    r = await adapter._k8s_get_session_status(pod_name="bioaf-notebook-55", namespace="bioaf-notebooks")
    assert r["status"] == "running"
