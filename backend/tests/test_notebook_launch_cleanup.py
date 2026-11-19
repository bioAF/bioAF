"""A notebook launch that fails releases what it created.

Pod creation precedes Service creation, so a Service failure leaves an accepted pod: it is released
before the error propagates. A pod-creation error deletes nothing: no resource is known to exist.
(Launches that fail later are released by the periodic pass; see test_notebook_reconcile.py.)
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.settings_contract import ComputeMode
from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod, notebook_service

POD, SVC = "bioaf-notebook-42", "bioaf-notebook-svc-42"


def _spec():
    return {
        "session_type": "jupyter",
        "session_id": 42,
        "user_id": 7,
        "resource_profile": "small",
        "cpu_cores": 2,
        "memory_gb": 4,
        "experiment_id": None,
        "organization_id": 1,
    }


@pytest.fixture
def adapter(mode_adapter_factory, monkeypatch):
    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._namespace_ready = True
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    monkeypatch.setattr(provider, "ensure_notebook_namespace", AsyncMock())
    monkeypatch.setattr(provider, "_ensure_gcs_secret", lambda ns: False)
    return provider


class LaunchCore(FakeCore):
    """FakeCore that also accepts creates, storing what launch submits as real-looking objects."""

    def __init__(self, *, service_error=None, **kw):
        super().__init__(**kw)
        self.service_error = service_error
        self.delete_is_immediate = True

    def create_namespaced_pod(self, namespace, body, **_):
        self.calls.append(("create", "pod", body["metadata"]["name"]))
        labels = body["metadata"]["labels"]
        self.pods[POD] = notebook_pod(42, int(labels["bioaf.io/user"]), labels["bioaf.io/type"], phase="Pending")

    def create_namespaced_service(self, namespace, body, **_):
        self.calls.append(("create", "service", body["metadata"]["name"]))
        if self.service_error:
            raise self.service_error
        self.services[SVC] = notebook_service(42)


@pytest.mark.asyncio
async def test_a_service_failure_after_the_pod_was_created_releases_the_pod(adapter, monkeypatch):
    core = LaunchCore(service_error=ApiException(status=500, reason="boom"))
    monkeypatch.setattr(adapter, "_get_k8s_core_client", lambda: core)

    with pytest.raises(ApiException):
        await adapter._k8s_launch_session(_spec())

    assert ("delete", "pod", POD, "bioaf-notebooks", "pod-uid-1") in core.calls
    assert POD not in core.pods


@pytest.mark.asyncio
async def test_a_pod_creation_error_deletes_nothing(adapter, monkeypatch):
    core = LaunchCore()

    def refuse(namespace, body, **_):
        raise ApiException(status=403, reason="Forbidden")

    core.create_namespaced_pod = refuse
    monkeypatch.setattr(adapter, "_get_k8s_core_client", lambda: core)

    with pytest.raises(ApiException):
        await adapter._k8s_launch_session(_spec())
    assert core.deletes() == []
