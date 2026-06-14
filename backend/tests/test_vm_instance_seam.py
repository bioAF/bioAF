"""Stage 5: the cloud-neutral VmInstance seam.

GCE VM lifecycle is now a GceVmInstance (implements the VmInstance primitive:
provision/delete/inspect/list_instances). GCEWorkNodeProvider rides on it, exposing
the same operations under the WorkNodeProvider names the service layer consumes.
Stage 6d adds Ec2VmInstance behind the same seam. Runs in local mode (no GCE API).
"""

from __future__ import annotations

import pytest

from app.adapters.base import VmInstance, WorkNodeProvider
from app.adapters.capabilities import ProviderCapabilities
from app.adapters.models import VmInfo, VmStatus
from app.adapters.work_nodes.gce import GCEWorkNodeProvider, GceVmInstance


def test_gce_vm_instance_implements_the_vm_primitive():
    assert issubclass(GceVmInstance, VmInstance)
    # The VM primitive is not a work-node: work-node orchestration rides on top.
    assert not issubclass(GceVmInstance, WorkNodeProvider)


def test_work_node_provider_rides_on_vm_instance():
    p = GCEWorkNodeProvider()
    assert isinstance(p, VmInstance)
    assert isinstance(p, WorkNodeProvider)
    assert p.capabilities() == ProviderCapabilities(work_nodes=True)


@pytest.mark.asyncio
async def test_provision_inspect_list_local():
    vm = GceVmInstance()  # local mode (BIOAF_COMPUTE_MODE=local from conftest)
    info = await vm.provision({"session_id": 1, "image_uri": "img"})
    assert isinstance(info, VmInfo)
    assert info.instance_name.startswith("bioaf-worknode-local-")

    status = await vm.inspect(info.instance_name, "us-central1-a")
    assert isinstance(status, VmStatus)

    listed = await vm.list_instances()
    assert any(s.instance_name == info.instance_name for s in listed)


@pytest.mark.asyncio
async def test_work_node_names_delegate_to_vm_primitive():
    p = GCEWorkNodeProvider()
    # The WorkNodeProvider-named methods are thin delegators to the VmInstance ops.
    info = await p.launch_vm({"session_id": 2, "image_uri": "img"})
    assert isinstance(info, VmInfo)
    status = await p.get_vm_status(info.instance_name, "us-central1-a")
    assert isinstance(status, VmStatus)
