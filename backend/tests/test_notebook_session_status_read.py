"""The adapter's status read for an interactive session uses the shared launch assessment.

Pod phase Unknown, or a failed read, is reported as unknown, not as an error. A missing pod is an
error. "running" requires the notebook container itself and a LoadBalancer address.
"""

import time
from unittest.mock import MagicMock

import pytest

from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.settings_contract import ComputeMode
from tests.support.notebook_k8s_fakes import FakeCore, notebook_pod

ARGS = {"session_id": 42, "pod_name": "bioaf-notebook-42", "namespace": "bioaf-notebooks", "session_type": "jupyter"}


@pytest.fixture
def adapter(mode_adapter_factory):
    provider = mode_adapter_factory(KubernetesNotebookProvider, mode=ComputeMode.KUBERNETES)
    provider._api_client = MagicMock()
    provider._client_created_at = time.monotonic()
    return provider


async def _read(adapter, core, url=None):
    adapter._get_k8s_core_client = lambda: core
    adapter._resolve_service_url = MagicMock(return_value=url)
    return await adapter._k8s_get_session_status(**ARGS)


@pytest.mark.asyncio
async def test_pod_phase_unknown_is_unknown_not_error(adapter):
    core = FakeCore(
        pods=[notebook_pod(phase="Unknown", ready=False, notebook=None)], events=[("TriggeredScaleUp", "x")]
    )
    assert (await _read(adapter, core))["status"] == "unknown"


@pytest.mark.asyncio
async def test_a_failed_read_is_unknown(adapter):
    core = FakeCore(pods=[notebook_pod()], errors={("read", "pod", "bioaf-notebook-42"): 500})
    assert (await _read(adapter, core))["status"] == "unknown"


@pytest.mark.asyncio
async def test_a_missing_pod_is_an_error_with_a_reason(adapter):
    result = await _read(adapter, FakeCore())
    assert result["status"] == "error"
    assert "no longer exists" in result["failure_message"]


@pytest.mark.asyncio
async def test_ready_without_an_address_is_still_starting(adapter):
    result = await _read(adapter, FakeCore(pods=[notebook_pod()]), url=None)
    assert result["status"] == "starting"


@pytest.mark.asyncio
async def test_ready_with_an_address_is_running(adapter):
    result = await _read(adapter, FakeCore(pods=[notebook_pod()]), url="http://1.2.3.4:8888")
    assert (result["status"], result["access_url"]) == ("running", "http://1.2.3.4:8888")


@pytest.mark.asyncio
async def test_a_sidecar_alone_running_is_starting(adapter):
    core = FakeCore(pods=[notebook_pod(phase="Running", ready=True, notebook="waiting")])
    assert (await _read(adapter, core, url="http://1.2.3.4:8888"))["status"] == "starting"


@pytest.mark.asyncio
async def test_a_confirmed_failure_carries_its_reason_and_skips_the_address(adapter):
    core = FakeCore(
        pods=[notebook_pod(phase="Failed", ready=False, notebook="terminated", notebook_reason="OOMKilled")]
    )
    result = await _read(adapter, core, url="http://nope")
    assert (result["status"], result["failure_reason"]) == ("error", "oom_killed")
    adapter._resolve_service_url.assert_not_called()


@pytest.mark.asyncio
async def test_a_starting_pod_reports_an_actionable_cause_for_a_later_timeout(adapter):
    core = FakeCore(
        pods=[notebook_pod(phase="Pending", ready=False, notebook=None)],
        events=[("FailedScaleUp", "GCE out of resources")],
    )
    result = await _read(adapter, core)
    assert result["status"] == "starting"
    assert (result["waiting_reason"], result["waiting_message"]) == ("resource_exhausted", "GCE out of resources")


@pytest.mark.asyncio
async def test_a_normal_scale_up_event_is_not_reported_as_a_cause(adapter):
    core = FakeCore(
        pods=[notebook_pod(phase="Pending", ready=False, notebook=None)],
        events=[("TriggeredScaleUp", "Pod triggered scale-up")],
    )
    result = await _read(adapter, core)
    assert result["status"] == "starting"
    assert "waiting_reason" not in result
