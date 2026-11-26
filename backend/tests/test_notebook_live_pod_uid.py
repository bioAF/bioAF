"""Plan 03.5A: a notebook is opened only on the running Pod that carries its recorded identity."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider, notebook_workload
from app.exceptions import ConnectionUnavailableError
from app.settings_contract import ComputeMode
from tests.support.mode_adapters import build_mode_adapter

W = notebook_workload(41, 5, "jupyter", organization_id=3, namespace="bioaf-notebooks", pod_name="bioaf-notebook-41")
LABELS = {"bioaf.io/session": "41", "bioaf.io/user": "5", "bioaf.io/type": "jupyter", "bioaf.io/organization": "3"}


def _pod(labels=LABELS, phase="Running", deleting=None, uid="uid-1"):
    return SimpleNamespace(
        metadata=SimpleNamespace(labels=labels, uid=uid, deletion_timestamp=deleting),
        status=SimpleNamespace(phase=phase),
    )


async def _uid(read):
    provider = build_mode_adapter(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    core = MagicMock()
    if isinstance(read, Exception):
        core.read_namespaced_pod.side_effect = read
    else:
        core.read_namespaced_pod.return_value = read
    with patch.object(provider, "_get_k8s_core_client", return_value=core):
        return await provider.live_pod_uid(W), core


@pytest.mark.asyncio
async def test_the_owned_running_pod_gives_its_uid():
    uid, core = await _uid(_pod())
    assert uid == "uid-1"
    assert core.read_namespaced_pod.call_args.args[:2] == ("bioaf-notebook-41", "bioaf-notebooks")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pod",
    [
        _pod(labels={**LABELS, "bioaf.io/session": "42"}),
        _pod(labels={**LABELS, "bioaf.io/user": "6"}),
        _pod(labels={**LABELS, "bioaf.io/organization": "9"}),
        _pod(phase="Pending"),
        _pod(deleting="2026-11-25T00:00:00Z"),
        _pod(uid=None),
    ],
)
async def test_any_other_pod_is_refused(pod):
    with pytest.raises(ConnectionUnavailableError):
        await _uid(pod)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ApiException(status=404), ApiException(status=403), TimeoutError()])
async def test_an_unreadable_pod_is_refused(error):
    with pytest.raises(ConnectionUnavailableError):
        await _uid(error)
